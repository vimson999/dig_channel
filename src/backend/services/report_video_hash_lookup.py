from types import SimpleNamespace

from sqlalchemy import or_

from models import db
from models.report_video import ReportVideoReport, ReportVideoTask


def source_hashes_from_report(report):
    hashes = []
    candidates = []
    meta = report.raw_meta if isinstance(report.raw_meta, dict) else {}
    candidates.extend([meta.get("auto_asset"), meta.get("auto_scan")])
    if report.task and isinstance(report.task.result, dict):
        candidates.extend([report.task.result.get("auto_asset"), report.task.result.get("auto_scan")])
    for item in candidates:
        if isinstance(item, dict) and item.get("source_sha256"):
            hashes.append(str(item["source_sha256"]))
    return hashes


def lookup_existing_reports_by_source_hash(source_hashes=None, limit=5000):
    hashes = _normalize_hashes(source_hashes)
    if not hashes:
        return {}
    if _is_postgres():
        return _lookup_existing_reports_postgres(hashes, limit=limit)
    return _lookup_existing_reports_fallback(hashes, limit=limit)


def _lookup_existing_reports_postgres(hashes, *, limit):
    report_asset_hash = _json_path_text(ReportVideoReport.raw_meta, "{auto_asset,source_sha256}")
    report_scan_hash = _json_path_text(ReportVideoReport.raw_meta, "{auto_scan,source_sha256}")
    task_asset_hash = _json_path_text(ReportVideoTask.result, "{auto_asset,source_sha256}")
    task_scan_hash = _json_path_text(ReportVideoTask.result, "{auto_scan,source_sha256}")

    query = (
        db.session.query(
            ReportVideoReport.id.label("id"),
            ReportVideoReport.task_id.label("task_id"),
            ReportVideoReport.parse_status.label("parse_status"),
            ReportVideoReport.ai_status.label("ai_status"),
            ReportVideoTask.status.label("task_status"),
            report_asset_hash.label("report_asset_hash"),
            report_scan_hash.label("report_scan_hash"),
            task_asset_hash.label("task_asset_hash"),
            task_scan_hash.label("task_scan_hash"),
        )
        .outerjoin(ReportVideoTask, ReportVideoTask.id == ReportVideoReport.task_id)
        .order_by(ReportVideoReport.created_at.desc())
    )
    query = query.filter(or_(
        report_asset_hash.in_(hashes),
        report_scan_hash.in_(hashes),
        task_asset_hash.in_(hashes),
        task_scan_hash.in_(hashes),
    )).limit(limit)

    result = {}
    for row in query.all():
        snapshot = SimpleNamespace(
            id=row.id,
            task_id=row.task_id,
            parse_status=row.parse_status,
            ai_status=row.ai_status,
            task_status=row.task_status,
            task=None,
        )
        for source_hash in (
            row.report_asset_hash,
            row.report_scan_hash,
            row.task_asset_hash,
            row.task_scan_hash,
        ):
            if source_hash and source_hash not in result:
                result[str(source_hash)] = snapshot
    return result


def _lookup_existing_reports_fallback(hashes, *, limit):
    rows = (
        db.session.query(
            ReportVideoReport.id.label("id"),
            ReportVideoReport.task_id.label("task_id"),
            ReportVideoReport.parse_status.label("parse_status"),
            ReportVideoReport.ai_status.label("ai_status"),
            ReportVideoReport.raw_meta.label("raw_meta"),
            ReportVideoTask.status.label("task_status"),
            ReportVideoTask.result.label("task_result"),
        )
        .outerjoin(ReportVideoTask, ReportVideoTask.id == ReportVideoReport.task_id)
        .order_by(ReportVideoReport.created_at.desc())
        .limit(limit)
        .all()
    )
    result = {}
    hash_set = set(hashes)
    for row in rows:
        report = SimpleNamespace(
            raw_meta=row.raw_meta,
            task=SimpleNamespace(result=row.task_result) if row.task_result is not None else None,
        )
        report_hashes = source_hashes_from_report(report)
        if hash_set and not hash_set.intersection(report_hashes):
            continue
        snapshot = SimpleNamespace(
            id=row.id,
            task_id=row.task_id,
            parse_status=row.parse_status,
            ai_status=row.ai_status,
            task_status=row.task_status,
            task=None,
        )
        for source_hash in report_hashes:
            result.setdefault(source_hash, snapshot)
    return result


def _normalize_hashes(source_hashes):
    seen = set()
    result = []
    for item in source_hashes or []:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _json_path_text(column, path):
    return column.op("#>>")(path)


def _is_postgres():
    try:
        bind = db.session.get_bind()
    except Exception:
        return False
    return bool(bind and bind.dialect.name == "postgresql")
