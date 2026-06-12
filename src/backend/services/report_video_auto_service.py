import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from models import db
from models.report_video import ReportVideoOutput, ReportVideoReport, ReportVideoTask
from services.report_video_service import PROJECT_ROOT, rel_path, report_code, report_dir, sanitize_filename


AUTO_CONFIG_PATH = PROJECT_ROOT / "data" / "report_video_auto_scan.json"


DEFAULT_AUTO_SCAN_CONFIG = {
    "enabled": False,
    "scan_dir": "伯恩斯坦",
    "schedule_time": "00:00",
    "interval_minutes": None,
    "max_files_per_run": 2,
    "output_ratios": ["16:9"],
    "default_segment_count": 4,
    "style": "professional_finance",
    "generate_subtitles": True,
}


TERMINAL_FAILURE_STATUSES = {"failed", "parse_failed", "ai_failed", "video_failed", "topic_failed"}


def _ensure_config_dir():
    AUTO_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _normalize_scan_dir(value):
    raw = str(value or DEFAULT_AUTO_SCAN_CONFIG["scan_dir"]).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _safe_rel_or_abs(path):
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_auto_scan_config():
    config = dict(DEFAULT_AUTO_SCAN_CONFIG)
    if AUTO_CONFIG_PATH.exists():
        try:
            saved = json.loads(AUTO_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
    config["scan_dir"] = str(config.get("scan_dir") or DEFAULT_AUTO_SCAN_CONFIG["scan_dir"])
    config["scan_dir_abs"] = str(_normalize_scan_dir(config["scan_dir"]))
    config["output_ratios"] = [item for item in (config.get("output_ratios") or ["16:9"]) if item in {"16:9", "9:16"}] or ["16:9"]
    config["default_segment_count"] = max(3, min(int(config.get("default_segment_count") or 4), 6))
    max_files = config.get("max_files_per_run")
    config["max_files_per_run"] = max(1, int(max_files or 1))
    interval = config.get("interval_minutes")
    config["interval_minutes"] = int(interval) if interval not in (None, "", 0, "0") else None
    return config


def save_auto_scan_config(payload):
    current = load_auto_scan_config()
    next_config = dict(current)
    allowed = {
        "enabled",
        "scan_dir",
        "schedule_time",
        "interval_minutes",
        "max_files_per_run",
        "output_ratios",
        "default_segment_count",
        "style",
        "generate_subtitles",
    }
    for key in allowed:
        if key in payload:
            next_config[key] = payload[key]
    next_config["updated_at"] = datetime.utcnow().isoformat()
    next_config.pop("scan_dir_abs", None)
    normalized = load_auto_scan_config_from_dict(next_config)
    _ensure_config_dir()
    persisted = dict(normalized)
    persisted.pop("scan_dir_abs", None)
    AUTO_CONFIG_PATH.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_auto_scan_config()


def load_auto_scan_config_from_dict(value):
    config = dict(DEFAULT_AUTO_SCAN_CONFIG)
    config.update(value or {})
    scan_dir = _normalize_scan_dir(config.get("scan_dir"))
    config["scan_dir"] = str(config.get("scan_dir") or DEFAULT_AUTO_SCAN_CONFIG["scan_dir"])
    config["scan_dir_abs"] = str(scan_dir)
    config["output_ratios"] = [item for item in (config.get("output_ratios") or ["16:9"]) if item in {"16:9", "9:16"}] or ["16:9"]
    config["default_segment_count"] = max(3, min(int(config.get("default_segment_count") or 4), 6))
    config["max_files_per_run"] = max(1, int(config.get("max_files_per_run") or 1))
    interval = config.get("interval_minutes")
    config["interval_minutes"] = int(interval) if interval not in (None, "", 0, "0") else None
    return config


def list_scan_candidates(config=None):
    config = config or load_auto_scan_config()
    scan_dir = Path(config["scan_dir_abs"])
    if not scan_dir.exists() or not scan_dir.is_dir():
        return []
    return sorted(path for path in scan_dir.glob("*.pdf") if path.is_file())


def _existing_report_by_hash(source_hash):
    if not source_hash:
        return None
    return _existing_reports_by_hash().get(source_hash)


def _existing_reports_by_hash():
    result = {}
    for report in ReportVideoReport.query.order_by(ReportVideoReport.created_at.desc()).limit(1000).all():
        meta = report.raw_meta or {}
        auto = meta.get("auto_scan") if isinstance(meta, dict) else None
        source_hash = auto.get("source_sha256") if isinstance(auto, dict) else None
        if source_hash and source_hash not in result:
            result[source_hash] = report
    return result


def _report_has_completed_video(report):
    if not report:
        return False
    return (
        ReportVideoOutput.query
        .filter(ReportVideoOutput.task_id == report.task_id)
        .filter(ReportVideoOutput.status == "completed")
        .count()
    ) > 0


def classify_pdf_for_auto_scan(path, existing_by_hash=None):
    source_path = Path(path).resolve()
    source_hash = _file_sha256(source_path)
    existing = (existing_by_hash or {}).get(source_hash) if existing_by_hash is not None else _existing_report_by_hash(source_hash)
    if existing and _report_has_completed_video(existing):
        return {
            "path": str(source_path),
            "display_path": _safe_rel_or_abs(source_path),
            "file_name": source_path.name,
            "source_sha256": source_hash,
            "status": "completed",
            "task_id": existing.task_id,
            "report_id": existing.id,
            "reason": "已生成过视频",
        }
    if existing and existing.task and existing.task.status not in TERMINAL_FAILURE_STATUSES:
        return {
            "path": str(source_path),
            "display_path": _safe_rel_or_abs(source_path),
            "file_name": source_path.name,
            "source_sha256": source_hash,
            "status": "queued_or_processing",
            "task_id": existing.task_id,
            "report_id": existing.id,
            "reason": f"已有任务：{existing.task.status}",
        }
    return {
        "path": str(source_path),
        "display_path": _safe_rel_or_abs(source_path),
        "file_name": source_path.name,
        "source_sha256": source_hash,
        "status": "new",
        "reason": "待制作",
    }


def auto_scan_preview(config=None):
    config = config or load_auto_scan_config()
    candidates = list_scan_candidates(config)
    existing_by_hash = _existing_reports_by_hash()
    rows = [classify_pdf_for_auto_scan(path, existing_by_hash=existing_by_hash) for path in candidates]
    return {
        "config": config,
        "scan_dir_exists": Path(config["scan_dir_abs"]).is_dir(),
        "total_pdf_count": len(candidates),
        "new_count": sum(1 for row in rows if row["status"] == "new"),
        "completed_count": sum(1 for row in rows if row["status"] == "completed"),
        "queued_count": sum(1 for row in rows if row["status"] == "queued_or_processing"),
        "items": rows,
    }


def create_task_for_source_pdf(path, source_hash, config=None, trigger="manual"):
    config = config or load_auto_scan_config()
    source_path = Path(path).resolve()
    task = ReportVideoTask(
        task_name=source_path.stem[:240],
        video_type="single_report",
        default_segment_count=config.get("default_segment_count") or 4,
        output_ratios=config.get("output_ratios") or ["16:9"],
        style=config.get("style") or "professional_finance",
        generate_subtitles=bool(config.get("generate_subtitles", True)),
        status="parsing",
        result={
            "auto_scan": {
                "source_path": _safe_rel_or_abs(source_path),
                "source_sha256": source_hash,
                "trigger": trigger,
                "created_at": datetime.utcnow().isoformat(),
            }
        },
    )
    db.session.add(task)
    db.session.flush()

    code = report_code(1)
    target_dir = report_dir(task.id, code)
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = target_dir / "original.pdf"
    shutil.copyfile(source_path, pdf_path)
    safe_name = sanitize_filename(source_path.name)
    named_copy = target_dir / safe_name
    if named_copy != pdf_path:
        try:
            shutil.copyfile(pdf_path, named_copy)
        except OSError:
            pass

    report = ReportVideoReport(
        task_id=task.id,
        report_code=code,
        file_name=source_path.name,
        file_path=rel_path(pdf_path),
        parse_status="parsing",
        ai_status="ai_pending",
        raw_meta={
            "queued_at": datetime.utcnow().isoformat(),
            "async": True,
            "auto_scan": {
                "source_path": _safe_rel_or_abs(source_path),
                "source_sha256": source_hash,
                "trigger": trigger,
            },
        },
    )
    db.session.add(report)
    db.session.flush()
    return task, report
