import hashlib
import os
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import and_, or_

from models import db
from models.report_video import ReportVideoReport, ReportVideoTask
from models.zsxq_sync import ZsxqAttachment, ZsxqSyncRun, ZsxqTopic
from services.report_video_hash_lookup import (
    lookup_existing_reports_by_source_hash,
    source_hashes_from_report,
)
from services.report_video_service import PROJECT_ROOT, rel_path, report_code, report_dir, sanitize_filename
from services.zsxq_sync_service import zsxq_sync_service


DEFAULT_ASSET_SCAN_CONFIG = {
    "max_files": 50,
    "tab_label_keyword": "外资研报",
    "output_ratios": ["16:9"],
    "default_segment_count": 4,
    "style": "professional_finance",
    "generate_subtitles": True,
}

THEME_KEYWORDS = [
    ("电力能源", ("电力", "电网", "光伏", "风电", "储能", "煤炭", "电池", "新能源", "电气设备")),
    ("半导体", ("半导体", "芯片", "晶圆", "封测", "存储", "算力", "设备", "材料", "MLCC", "模拟芯片")),
    ("AI算力", ("AI", "人工智能", "大模型", "算力", "服务器", "数据中心", "GPU", "光模块")),
    ("汽车产业", ("汽车", "智能驾驶", "新能源车", "零部件", "车企", "电动车", "特斯拉")),
    ("医药医疗", ("医药", "医疗", "创新药", "CXO", "器械", "医院", "医保")),
    ("消费", ("消费", "食品饮料", "白酒", "零售", "旅游", "餐饮", "家电")),
    ("金融地产", ("银行", "保险", "券商", "地产", "房地产", "信托")),
    ("互联网传媒", ("互联网", "游戏", "传媒", "广告", "电商", "短剧", "平台")),
    ("港股海外", ("港股", "美股", "海外", "美元", "日股", "欧洲", "东南亚")),
]


def preview_zsxq_report_assets(payload=None):
    """Preview report PDF assets from a sync run or a date backfill without writing DB rows."""
    service = ZsxqReportAssetService()
    return service.preview(payload or {})


def create_zsxq_report_asset_batch(payload=None):
    """Create a reusable report-video asset batch from downloaded ZSXQ PDF reports."""
    service = ZsxqReportAssetService()
    return service.create_batch(payload or {})


def should_auto_scan_after_sync(payload=None, result=None):
    if os.environ.get("ZSXQ_REPORT_ASSET_SCAN_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return False
    files = _downloaded_pdf_files_from_result(result)
    return bool(files)


def build_sync_run_asset_scan_payload(run_id, payload=None):
    payload = payload or {}
    config = dict(DEFAULT_ASSET_SCAN_CONFIG)
    max_files = payload.get("report_asset_scan_max_files") or os.environ.get("ZSXQ_REPORT_ASSET_SCAN_MAX_FILES")
    if max_files not in (None, ""):
        try:
            config["max_files"] = max(1, min(int(max_files), 200))
        except (TypeError, ValueError):
            pass
    tab_keyword = payload.get("report_asset_scan_tab_keyword") or os.environ.get("ZSXQ_REPORT_ASSET_SCAN_TAB_KEYWORD")
    if tab_keyword is not None:
        config["tab_label_keyword"] = str(tab_keyword).strip()
    return {
        "source": "sync_run",
        "run_id": str(run_id),
        "trigger": payload.get("trigger") or "zsxq_sync_complete",
        "parse_pdfs": _truthy(payload.get("report_asset_scan_parse_pdfs", True)),
        "config": config,
    }


class ZsxqReportAssetService:
    def preview(self, payload):
        normalized = self._normalize_payload(payload)
        candidates = self._candidate_items(normalized)
        existing_by_hash = self._existing_reports_by_hash(item.get("source_sha256") for item in candidates)
        items = [self._classify_candidate(item, existing_by_hash) for item in candidates]
        limited_items = items[: normalized["config"]["max_files"]]
        return self._summary(normalized, items, limited_items, task=None, created_reports=[])

    def create_batch(self, payload):
        normalized = self._normalize_payload(payload)
        candidates = self._candidate_items(normalized)
        existing_by_hash = self._existing_reports_by_hash(item.get("source_sha256") for item in candidates)
        classified = [self._classify_candidate(item, existing_by_hash) for item in candidates]
        new_items = [item for item in classified if item["status"] == "new"][: normalized["config"]["max_files"]]

        if not new_items:
            return self._summary(normalized, classified, [], task=None, created_reports=[])

        task = ReportVideoTask(
            task_name=self._task_name(normalized),
            video_type="daily_digest",
            default_segment_count=normalized["config"]["default_segment_count"],
            output_ratios=normalized["config"]["output_ratios"],
            style=normalized["config"]["style"],
            generate_subtitles=normalized["config"]["generate_subtitles"],
            status="parsing" if normalized["parse_pdfs"] else "asset_indexed",
            result={
                "auto_asset": {
                    "source": normalized["source"],
                    "trigger": normalized["trigger"],
                    "run_id": normalized.get("run_id"),
                    "target_date": normalized.get("target_date"),
                    "created_at": datetime.utcnow().isoformat(),
                    "parse_pdfs": normalized["parse_pdfs"],
                }
            },
        )
        db.session.add(task)
        db.session.flush()

        created_reports = []
        for index, item in enumerate(new_items, start=1):
            report = self._create_report(task, index, item, normalized)
            created_reports.append(report)

        task.result = {
            **(task.result or {}),
            "asset_scan": {
                "candidate_count": len(classified),
                "new_count": len(new_items),
                "existing_count": sum(1 for item in classified if item["status"] == "existing"),
                "skipped_count": sum(1 for item in classified if item["status"] == "skipped"),
                "created_report_ids": [report.id for report in created_reports],
                "updated_at": datetime.utcnow().isoformat(),
            },
        }
        task.updated_at = datetime.utcnow()
        summary = self._summary(
            normalized,
            classified,
            new_items,
            task=task,
            created_reports=created_reports,
        )
        db.session.commit()
        return summary

    def _normalize_payload(self, payload):
        payload = payload or {}
        config = dict(DEFAULT_ASSET_SCAN_CONFIG)
        if isinstance(payload.get("config"), dict):
            config.update({key: value for key, value in payload["config"].items() if value is not None})
        config["max_files"] = max(1, min(int(config.get("max_files") or 50), 200))
        config["tab_label_keyword"] = str(config.get("tab_label_keyword") or "").strip()
        config["default_segment_count"] = max(3, min(int(config.get("default_segment_count") or 4), 6))
        ratios = [item for item in (config.get("output_ratios") or ["16:9"]) if item in {"16:9", "9:16"}]
        config["output_ratios"] = ratios or ["16:9"]
        config["style"] = str(config.get("style") or "professional_finance")
        config["generate_subtitles"] = _truthy(config.get("generate_subtitles", True))

        source = str(payload.get("source") or "sync_run").strip()
        if source not in {"sync_run", "daily_backfill", "manual"}:
            raise ValueError(f"不支持的资产扫描来源: {source}")

        normalized = {
            "source": source,
            "trigger": str(payload.get("trigger") or "manual"),
            "parse_pdfs": _truthy(payload.get("parse_pdfs", True)),
            "config": config,
        }
        if source == "sync_run":
            run_id = str(payload.get("run_id") or "").strip()
            if not run_id:
                raise ValueError("sync_run 资产扫描需要 run_id")
            normalized["run_id"] = run_id
        else:
            normalized["target_date"] = _normalize_target_date(payload.get("target_date"))
        return normalized

    def _candidate_items(self, normalized):
        if normalized["source"] == "sync_run":
            return self._candidate_items_from_run(
                normalized["run_id"],
                normalized["config"],
                normalized=normalized,
            )
        return self._candidate_items_from_date(normalized["target_date"], normalized["config"])

    def _candidate_items_from_run(self, run_id, config, normalized=None):
        cleanable = zsxq_sync_service.downloaded_pdf_files_for_run(run_id)
        run = SimpleNamespace(**(cleanable.get("run") or {}))
        if normalized is not None:
            normalized["_run_label"] = run.group_name or run.group_id
        files = cleanable.get("files") or []
        items = []
        for file_item in files:
            path = _resolve_existing_pdf(file_item.get("path"))
            if not path:
                continue
            topic = self._topic_for_file(run, file_item)
            if not self._matches_tab_keyword(topic, run, config):
                continue
            items.append(self._candidate_from_path(
                path,
                file_item.get("name") or path.name,
                run=run,
                topic=topic,
                attachment_id=file_item.get("attachment_id"),
            ))
        return _dedupe_candidates(items)

    def _candidate_items_from_date(self, target_date, config):
        start, end = _date_range(target_date)
        query = (
            db.session.query(
                ZsxqAttachment.id.label("attachment_id"),
                ZsxqAttachment.group_id.label("attachment_group_id"),
                ZsxqAttachment.tab_id.label("attachment_tab_id"),
                ZsxqAttachment.topic_id.label("attachment_topic_id"),
                ZsxqAttachment.name.label("attachment_name"),
                ZsxqAttachment.local_path.label("attachment_local_path"),
                ZsxqTopic.id.label("topic_row_id"),
                ZsxqTopic.group_name.label("topic_group_name"),
                ZsxqTopic.tab_id.label("topic_tab_id"),
                ZsxqTopic.tab_label.label("topic_tab_label"),
                ZsxqTopic.topic_id.label("topic_id"),
                ZsxqTopic.title.label("topic_title"),
                ZsxqTopic.content.label("topic_content"),
                ZsxqTopic.author_name.label("topic_author_name"),
                ZsxqTopic.create_time.label("topic_create_time"),
            )
            .outerjoin(
                ZsxqTopic,
                and_(
                    ZsxqTopic.group_id == ZsxqAttachment.group_id,
                    ZsxqTopic.tab_id == ZsxqAttachment.tab_id,
                    ZsxqTopic.topic_id == ZsxqAttachment.topic_id,
                ),
            )
            .filter(ZsxqAttachment.download_status == "downloaded")
            .filter(ZsxqAttachment.updated_at >= start)
            .filter(ZsxqAttachment.updated_at < end)
            .order_by(ZsxqAttachment.updated_at.desc(), ZsxqAttachment.created_at.desc())
            .limit(500)
        )
        items = []
        for row in query.all():
            path = _resolve_existing_pdf(row.attachment_local_path)
            if not path:
                continue
            attachment = SimpleNamespace(
                id=row.attachment_id,
                group_id=row.attachment_group_id,
                tab_id=row.attachment_tab_id,
                topic_id=row.attachment_topic_id,
                name=row.attachment_name,
            )
            topic = None
            if row.topic_row_id:
                topic = SimpleNamespace(
                    group_name=row.topic_group_name,
                    tab_id=row.topic_tab_id,
                    tab_label=row.topic_tab_label,
                    topic_id=row.topic_id,
                    title=row.topic_title,
                    content=row.topic_content,
                    author_name=row.topic_author_name,
                    create_time=row.topic_create_time,
                )
            if not self._matches_tab_keyword(topic, None, config):
                continue
            items.append(self._candidate_from_path(
                path,
                row.attachment_name or path.name,
                run=None,
                topic=topic,
                attachment_id=attachment.id,
                attachment=attachment,
            ))
        return _dedupe_candidates(items)

    def _topic_for_file(self, run, file_item):
        topic_id = str(file_item.get("topic_id") or "").strip()
        tab_id = str(file_item.get("tab_id") or run.tab_id or "").strip()
        if not topic_id or not tab_id:
            return None
        return (
            ZsxqTopic.query
            .with_entities(
                ZsxqTopic.group_name,
                ZsxqTopic.tab_id,
                ZsxqTopic.tab_label,
                ZsxqTopic.topic_id,
                ZsxqTopic.title,
                ZsxqTopic.content,
                ZsxqTopic.author_name,
                ZsxqTopic.create_time,
            )
            .filter_by(group_id=run.group_id, tab_id=tab_id, topic_id=topic_id)
            .first()
        )

    def _matches_tab_keyword(self, topic, run, config):
        keyword = config.get("tab_label_keyword")
        if not keyword:
            return True
        labels = []
        if topic:
            labels.append(topic.tab_label or "")
        if run:
            labels.append(run.tab_label or "")
        if not labels:
            return True
        return any(keyword in label for label in labels)

    def _candidate_from_path(self, path, file_name, *, run=None, topic=None, attachment_id=None, attachment=None):
        source_hash = _file_sha256(path)
        title_text = " ".join([
            str(file_name or ""),
            str(topic.title or "") if topic else "",
            str(topic.content or "") if topic else "",
        ]).strip()
        theme_hint = classify_theme_hint(title_text)
        return {
            "path": str(path),
            "display_path": _safe_rel_or_abs(path),
            "file_name": file_name or path.name,
            "source_sha256": source_hash,
            "theme_hint": theme_hint["theme"],
            "theme_keywords": theme_hint["matched_keywords"],
            "zsxq": {
                "run_id": run.id if run else None,
                "group_id": (run.group_id if run else None) or (attachment.group_id if attachment else None),
                "group_name": (run.group_name if run else None) or (topic.group_name if topic else None),
                "tab_id": (topic.tab_id if topic else None) or (attachment.tab_id if attachment else None),
                "tab_label": (topic.tab_label if topic else None) or (run.tab_label if run else None),
                "topic_id": (topic.topic_id if topic else None) or (attachment.topic_id if attachment else None),
                "topic_title": topic.title if topic else None,
                "topic_author": topic.author_name if topic else None,
                "topic_create_time": topic.create_time if topic else None,
                "attachment_id": attachment_id,
            },
        }

    def _classify_candidate(self, item, existing_by_hash):
        existing = existing_by_hash.get(item["source_sha256"])
        if existing:
            return {
                **item,
                "status": "existing",
                "reason": f"已存在资产记录：{existing.parse_status}",
                "task_id": existing.task_id,
                "report_id": existing.id,
                "parse_status": existing.parse_status,
            }
        return {**item, "status": "new", "reason": "待入库并解析"}

    def _existing_reports_by_hash(self, source_hashes=None):
        return lookup_existing_reports_by_source_hash(source_hashes, limit=5000)

    def _create_report(self, task, index, item, normalized):
        code = report_code(index)
        target_dir = report_dir(task.id, code)
        target_dir.mkdir(parents=True, exist_ok=True)
        source_path = Path(item["path"]).resolve()
        pdf_path = target_dir / "original.pdf"
        shutil.copyfile(source_path, pdf_path)

        safe_name = sanitize_filename(item["file_name"])
        named_copy = target_dir / safe_name
        if named_copy != pdf_path:
            try:
                shutil.copyfile(pdf_path, named_copy)
            except OSError:
                pass

        report = ReportVideoReport(
            task_id=task.id,
            report_code=code,
            file_name=item["file_name"],
            file_path=rel_path(pdf_path),
            parse_status="parsing" if normalized["parse_pdfs"] else "uploaded",
            ai_status="ai_pending",
            raw_meta={
                "queued_at": datetime.utcnow().isoformat(),
                "async": True,
                "auto_asset": {
                    "source": normalized["source"],
                    "trigger": normalized["trigger"],
                    "run_id": normalized.get("run_id"),
                    "target_date": normalized.get("target_date"),
                    "source_path": item["display_path"],
                    "source_sha256": item["source_sha256"],
                    "theme_hint": item["theme_hint"],
                    "theme_keywords": item["theme_keywords"],
                    "zsxq": item["zsxq"],
                },
            },
        )
        db.session.add(report)
        db.session.flush()
        return report

    def _task_name(self, normalized):
        if normalized["source"] == "sync_run":
            label = normalized.get("_run_label")
            if not label:
                run = (
                    ZsxqSyncRun.query
                    .with_entities(ZsxqSyncRun.group_name, ZsxqSyncRun.group_id)
                    .filter(ZsxqSyncRun.id == normalized["run_id"])
                    .one_or_none()
                )
                label = (run.group_name or run.group_id) if run else "知识星球"
            return f"{label} 研报资产扫描 {datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"[:240]
        return f"{normalized['target_date']} 研报资产补漏扫描"

    def _summary(self, normalized, classified_items, selected_items, *, task, created_reports):
        return {
            "source": normalized["source"],
            "trigger": normalized["trigger"],
            "parse_pdfs": normalized["parse_pdfs"],
            "run_id": normalized.get("run_id"),
            "target_date": normalized.get("target_date"),
            "config": normalized["config"],
            "candidate_count": len(classified_items),
            "new_count": sum(1 for item in classified_items if item["status"] == "new"),
            "existing_count": sum(1 for item in classified_items if item["status"] == "existing"),
            "skipped_count": sum(1 for item in classified_items if item["status"] == "skipped"),
            "selected_count": len(selected_items),
            "created_count": len(created_reports),
            "task_id": task.id if task else None,
            "report_ids": [report.id for report in created_reports],
            "items": classified_items[:200],
            "created": [
                {
                    "report_id": report.id,
                    "task_id": report.task_id,
                    "file_name": report.file_name,
                    "parse_status": report.parse_status,
                }
                for report in created_reports
            ],
        }


def classify_theme_hint(text):
    haystack = str(text or "").upper()
    best_theme = "其他"
    best_keywords = []
    for theme, keywords in THEME_KEYWORDS:
        matched = [keyword for keyword in keywords if str(keyword).upper() in haystack]
        if len(matched) > len(best_keywords):
            best_theme = theme
            best_keywords = matched
    return {"theme": best_theme, "matched_keywords": best_keywords}


def _source_hashes_from_report(report):
    return source_hashes_from_report(report)


def _resolve_existing_pdf(value):
    if not value:
        return None
    raw = str(value)
    if raw.startswith("browser-download:"):
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if path.suffix.lower() != ".pdf" or not path.exists() or not path.is_file():
        return None
    return path


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_rel_or_abs(path):
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(Path(path).resolve())


def _dedupe_candidates(items):
    seen = set()
    result = []
    for item in items:
        key = item.get("source_sha256") or item.get("path")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalize_target_date(value):
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value or "").strip()
    if not raw:
        return (date.today() - timedelta(days=1)).isoformat()
    datetime.strptime(raw, "%Y-%m-%d")
    return raw


def _date_range(value):
    target = datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.combine(target, time.min), datetime.combine(target + timedelta(days=1), time.min)


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _downloaded_pdf_files_from_result(result):
    if not isinstance(result, dict):
        return []
    run_result = result.get("run", {}).get("result") if isinstance(result.get("run"), dict) else None
    source = run_result if isinstance(run_result, dict) else result
    files = source.get("downloaded_pdf_files") or []
    if not files:
        for tab in source.get("tabs") or []:
            if isinstance(tab, dict):
                files.extend(tab.get("downloaded_pdf_files") or [])
    return files
