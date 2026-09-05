"""Parse, analyze, and persist ZSXQ list-export JSON snapshots.

The service intentionally keeps the source payload out of the database.  It
normalizes the bounded upload into relational projections and stores the exact
uploaded bytes as a user-scoped audit artifact for explicit source downloads.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from statistics import mean, median
from typing import Any, Iterable
import uuid

from flask import current_app
from sqlalchemy import desc, func

try:  # Optional; the lexicon fallback keeps minimal installs usable.
    import jieba as _jieba
except ImportError:  # pragma: no cover - depends on the deployment extra set.
    _jieba = None

from models import (
    ZsxqAnalysisItem,
    ZsxqAnalysisKeyword,
    ZsxqAnalysisRun,
    ZsxqAnalysisTopic,
    db,
)


SUPPORTED_KINDS = frozenset({"pdf", "audio"})
SUPPORTED_STATUSES = frozenset({"done", "pending", "failed", "processing"})
SOURCE_SCHEMA = "zsxq-list-export/v1"
ANALYSIS_VERSION = "zsxq-list-metadata/v1"
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_ITEM_COUNT = 5000
MAX_KEYWORDS = 80
MAX_SOURCE_FILENAME_LENGTH = 255

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_ROOT = PROJECT_ROOT / "data" / "zsxq_analysis"


class ZsxqAnalysisValidationError(ValueError):
    """Raised when an uploaded list export is not analyzable."""


@dataclass(frozen=True)
class NormalizedAnalysis:
    kind: str
    source_schema: str
    source_exported_at: datetime | None
    items: tuple[dict[str, Any], ...]
    summary: dict[str, Any]
    topics: tuple[dict[str, Any], ...]
    keywords: tuple[dict[str, Any], ...]
    analysis_version: str = ANALYSIS_VERSION


# These are deliberately small, explainable vocabulary hints for filenames.
# They are not presented as semantic understanding of the underlying files.
_KNOWN_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("数据中心", "phrase"),
    ("系统集成", "phrase"),
    ("人工智能", "phrase"),
    ("产业链", "phrase"),
    ("大模型", "phrase"),
    ("云计算", "phrase"),
    ("投资者日", "phrase"),
    ("电话会", "phrase"),
    ("策略会", "phrase"),
    ("闭门会", "phrase"),
    ("机器人", "phrase"),
    ("半导体", "phrase"),
    ("新能源", "phrase"),
    ("数据", "phrase"),
    ("算力", "phrase"),
    ("芯片", "phrase"),
    ("储能", "phrase"),
    ("光伏", "phrase"),
    ("汽车", "phrase"),
    ("医药", "phrase"),
    ("消费", "phrase"),
    ("房地产", "phrase"),
    ("银行", "phrase"),
    ("证券", "phrase"),
    ("保险", "phrase"),
    ("宏观", "phrase"),
    ("科技", "phrase"),
    ("业绩", "phrase"),
    ("中报", "phrase"),
    ("出海", "phrase"),
    ("跨境", "phrase"),
    ("电商", "phrase"),
    ("制造", "phrase"),
    ("金融", "phrase"),
    ("港股", "phrase"),
    ("美股", "phrase"),
    ("A股", "phrase"),
    ("AI", "latin"),
    ("AIDC", "latin"),
)
_KEYWORD_STOPWORDS = frozenset(
    {
        "文件",
        "资料",
        "下载",
        "线上",
        "交流",
        "参考",
        "横向",
        "未覆盖",
        "公司",
        "报告",
        "研究",
        "会议",
        "分享",
        "最新",
        "完整版",
        "音频",
        "附件",
    }
)
_LATIN_STOPWORDS = frozenset(
    {
        "PDF",
        "MP3",
        "M4A",
        "WAV",
        "HK",
        "SZ",
        "SH",
        "US",
        "TW",
        "KS",
        "OQ",
        "SS",
        "Q26",
    }
)
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_LATIN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9+&.-]{1,}(?![A-Za-z0-9])"
)
_TICKER_RE = re.compile(
    r"(?<!\d)(?:\d{3,6}\.(?:SZ|SH|HK|US|TW|KS|OQ|SS)|\d{4}HK)(?!\d)", re.I
)
_DATE_SUFFIX_RE = re.compile(
    r"(?:[-_ ]?(?:19|20)\d{2}[-_ ]?\d{2}[-_ ]?\d{2}|[-_ ]?\d{6,8})$"
)
_EXTENSION_RE = re.compile(r"\.(?:pdf|mp3|m4a|wav|flac|aac|ogg|opus)$", re.I)
_DUPLICATE_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")

if _jieba is not None:
    for _keyword, _category in _KNOWN_KEYWORDS:
        _jieba.add_word(_keyword)


def analyze_source_bytes(
    source_bytes: bytes,
    *,
    filename: str | None = None,
    expected_kind: str | None = None,
) -> NormalizedAnalysis:
    """Validate and analyze one bounded upload without opening a DB session."""

    del filename  # Reserved for future filename-specific diagnostics.
    if not isinstance(source_bytes, (bytes, bytearray)):
        raise ZsxqAnalysisValidationError("上传内容必须是 JSON 文件")
    if len(source_bytes) == 0:
        raise ZsxqAnalysisValidationError("上传文件不能为空")
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise ZsxqAnalysisValidationError("JSON 文件不能超过 5 MiB")

    try:
        payload = json.loads(bytes(source_bytes).decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ZsxqAnalysisValidationError("JSON 文件必须使用 UTF-8 编码") from exc
    except json.JSONDecodeError as exc:
        raise ZsxqAnalysisValidationError(
            f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列"
        ) from exc

    return normalize_export_payload(payload, expected_kind=expected_kind)


def normalize_export_payload(
    payload: Any,
    *,
    expected_kind: str | None = None,
) -> NormalizedAnalysis:
    """Normalize the known `zsxq-list-export/v1` contract."""

    if not isinstance(payload, dict):
        raise ZsxqAnalysisValidationError("JSON 顶层必须是对象")
    if payload.get("schema") != SOURCE_SCHEMA:
        raise ZsxqAnalysisValidationError(f"只支持 {SOURCE_SCHEMA} 数据")

    kind = _text(payload.get("kind"), "kind", max_length=16).lower()
    if kind not in SUPPORTED_KINDS:
        raise ZsxqAnalysisValidationError("kind 只能是 pdf 或 audio")
    if expected_kind and str(expected_kind).strip().lower() != kind:
        raise ZsxqAnalysisValidationError("上传文件类型与当前 Tab 不一致")

    exported_at_text = _text(
        payload.get("exportedAt"), "exportedAt", max_length=100
    )
    if not isinstance(payload.get("summary"), dict):
        raise ZsxqAnalysisValidationError("summary 必须是对象")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ZsxqAnalysisValidationError("items 必须是数组")
    if len(raw_items) > MAX_ITEM_COUNT:
        raise ZsxqAnalysisValidationError(f"单次最多分析 {MAX_ITEM_COUNT} 条记录")

    normalized_items: list[dict[str, Any]] = []
    seen_file_ids: set[str] = set()
    duplicate_file_id_count = 0
    for item_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise ZsxqAnalysisValidationError(f"items[{item_index}] 必须是对象")

        file_id = _text(
            raw_item.get("fileId"),
            f"items[{item_index}].fileId",
            max_length=160,
        )
        name = _text(
            raw_item.get("name"), f"items[{item_index}].name", max_length=2000
        )
        status = _text(
            raw_item.get("status"),
            f"items[{item_index}].status",
            max_length=24,
        ).lower()
        if status not in SUPPORTED_STATUSES:
            raise ZsxqAnalysisValidationError(
                f"items[{item_index}].status 不支持：{status}"
            )

        duplicate = file_id in seen_file_ids
        if duplicate:
            duplicate_file_id_count += 1
        seen_file_ids.add(file_id)

        upload_time_text = _optional_text(
            raw_item.get("uploadTime"), max_length=100
        )
        normalized_items.append(
            {
                "kind": kind,
                "item_index": item_index,
                "file_id": file_id,
                "topic_id": _optional_text(
                    raw_item.get("topicId"), max_length=160
                ),
                "name": name,
                "normalized_name": _normalize_filename(name),
                "group_name": _optional_text(
                    raw_item.get("groupName"), max_length=255
                ),
                "status": status,
                "download_count": _non_negative_int(
                    raw_item.get("downloadCount", 0),
                    f"items[{item_index}].downloadCount",
                ),
                "file_size_bytes": _non_negative_int(
                    raw_item.get("fileSize", 0),
                    f"items[{item_index}].fileSize",
                ),
                "duration_seconds": _non_negative_int(
                    raw_item.get("duration", 0),
                    f"items[{item_index}].duration",
                ),
                "upload_time_text": upload_time_text,
                "upload_time": _parse_datetime(
                    upload_time_text, f"items[{item_index}].uploadTime"
                ),
                "is_duplicate_file": duplicate,
            }
        )

    summary = _build_summary(normalized_items, duplicate_file_id_count)
    topics = _build_topic_projections(normalized_items)
    keywords = _build_keyword_projections(normalized_items, kind)
    return NormalizedAnalysis(
        kind=kind,
        source_schema=SOURCE_SCHEMA,
        source_exported_at=_parse_datetime(
            exported_at_text,
            "exportedAt",
        ),
        items=tuple(normalized_items),
        summary=summary,
        topics=tuple(topics),
        keywords=tuple(keywords),
    )


def create_analysis_run(
    *,
    user_id: int,
    source_bytes: bytes,
    source_filename: str | None = None,
    expected_kind: str | None = None,
) -> ZsxqAnalysisRun:
    """Persist one complete analysis in a short transaction."""

    if user_id is None:
        raise ZsxqAnalysisValidationError("缺少用户身份")
    normalized = analyze_source_bytes(
        source_bytes,
        filename=source_filename,
        expected_kind=expected_kind,
    )
    run_id = str(uuid.uuid4())
    source_filename = _display_filename(source_filename)
    source_sha256 = hashlib.sha256(bytes(source_bytes)).hexdigest()
    artifact_path, artifact_relative_path = _write_source_artifact(
        user_id=user_id,
        run_id=run_id,
        source_bytes=bytes(source_bytes),
    )

    summary = normalized.summary
    run = ZsxqAnalysisRun(
        id=run_id,
        user_id=int(user_id),
        kind=normalized.kind,
        source_schema=normalized.source_schema,
        source_filename=source_filename,
        source_exported_at=normalized.source_exported_at,
        source_sha256=source_sha256,
        source_size_bytes=len(source_bytes),
        source_artifact_path=artifact_relative_path,
        analysis_version=normalized.analysis_version,
        status="completed",
        item_count=summary["item_count"],
        done_count=summary["done_count"],
        pending_count=summary["pending_count"],
        failed_count=summary["failed_count"],
        processing_count=summary["processing_count"],
        total_download_count=summary["total_download_count"],
        download_count_min=summary["download_count_min"],
        download_count_max=summary["download_count_max"],
        download_count_avg=summary["download_count_avg"],
        download_count_median=summary["download_count_median"],
        total_file_size_bytes=summary["total_file_size_bytes"],
        total_duration_seconds=summary["total_duration_seconds"],
        duration_avg_seconds=summary["duration_avg_seconds"],
        duration_median_seconds=summary["duration_median_seconds"],
        duration_nonzero_count=summary["duration_nonzero_count"],
        unique_topic_count=summary["unique_topic_count"],
        duplicate_file_id_count=summary["duplicate_file_id_count"],
        period_start=summary["period_start"],
        period_end=summary["period_end"],
        completed_at=datetime.utcnow(),
    )

    item_rows = [
        ZsxqAnalysisItem(
            run_id=run_id,
            **{key: value for key, value in item.items() if key != "kind"},
        )
        for item in normalized.items
    ]
    topic_rows = [
        ZsxqAnalysisTopic(run_id=run_id, **topic)
        for topic in normalized.topics
    ]
    keyword_rows = [
        ZsxqAnalysisKeyword(
            run_id=run_id,
            **{key: value for key, value in keyword.items() if key != "kind"},
        )
        for keyword in normalized.keywords
    ]

    try:
        db.session.add(run)
        db.session.add_all(item_rows)
        db.session.add_all(topic_rows)
        db.session.add_all(keyword_rows)
        db.session.commit()
    except Exception:
        db.session.rollback()
        _remove_artifact(artifact_path)
        raise
    return run


def list_analysis_runs(
    *,
    user_id: int,
    kind: str | None,
    page: int,
    per_page: int,
) -> tuple[list[dict[str, Any]], int]:
    """Read only narrow structured columns for the history list."""

    normalized_kind = _validate_kind_filter(kind)
    query = db.session.query(
        ZsxqAnalysisRun.id,
        ZsxqAnalysisRun.kind,
        ZsxqAnalysisRun.source_filename,
        ZsxqAnalysisRun.status,
        ZsxqAnalysisRun.analysis_version,
        ZsxqAnalysisRun.source_sha256,
        ZsxqAnalysisRun.source_size_bytes,
        ZsxqAnalysisRun.item_count,
        ZsxqAnalysisRun.done_count,
        ZsxqAnalysisRun.pending_count,
        ZsxqAnalysisRun.failed_count,
        ZsxqAnalysisRun.processing_count,
        ZsxqAnalysisRun.total_download_count,
        ZsxqAnalysisRun.total_duration_seconds,
        ZsxqAnalysisRun.created_at,
        ZsxqAnalysisRun.completed_at,
    ).filter(ZsxqAnalysisRun.user_id == int(user_id))
    if normalized_kind:
        query = query.filter(ZsxqAnalysisRun.kind == normalized_kind)

    total = int(
        db.session.query(func.count(ZsxqAnalysisRun.id))
        .filter(ZsxqAnalysisRun.user_id == int(user_id))
        .filter(
            ZsxqAnalysisRun.kind == normalized_kind
            if normalized_kind
            else True
        )
        .scalar()
        or 0
    )
    rows = (
        query.order_by(
            desc(ZsxqAnalysisRun.created_at), desc(ZsxqAnalysisRun.id)
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return [_history_row_to_dict(row) for row in rows], total


def get_analysis_run(*, user_id: int, run_id: str) -> ZsxqAnalysisRun | None:
    return (
        db.session.query(ZsxqAnalysisRun)
        .filter(
            ZsxqAnalysisRun.id == str(run_id),
            ZsxqAnalysisRun.user_id == int(user_id),
        )
        .first()
    )


def build_analysis_run_detail(
    run: ZsxqAnalysisRun,
    *,
    keyword_limit: int = MAX_KEYWORDS,
    topic_limit: int = 100,
    top_item_limit: int = 20,
) -> dict[str, Any]:
    """Build one detail response from structured projections only."""

    keywords = (
        db.session.query(ZsxqAnalysisKeyword)
        .filter(ZsxqAnalysisKeyword.run_id == run.id)
        .order_by(ZsxqAnalysisKeyword.rank.asc())
        .limit(keyword_limit)
        .all()
    )
    topics = (
        db.session.query(ZsxqAnalysisTopic)
        .filter(ZsxqAnalysisTopic.run_id == run.id)
        .order_by(ZsxqAnalysisTopic.rank.asc())
        .limit(topic_limit)
        .all()
    )
    top_items = (
        db.session.query(ZsxqAnalysisItem)
        .filter(ZsxqAnalysisItem.run_id == run.id)
        .order_by(
            ZsxqAnalysisItem.download_count.desc(),
            ZsxqAnalysisItem.upload_time.desc().nullslast(),
            ZsxqAnalysisItem.item_index.asc(),
        )
        .limit(top_item_limit)
        .all()
    )
    timeline = _build_timeline(run.id)
    summary = run.to_summary_dict()
    summary["duration_distribution"] = (
        _build_duration_distribution(run.id)
        if run.kind == "audio"
        else []
    )
    try:
        summary["source_available"] = resolve_source_artifact(run).is_file()
    except FileNotFoundError:
        summary["source_available"] = False
    return {
        "run": summary,
        "keywords": [row.to_dict() for row in keywords],
        "topics": [row.to_dict() for row in topics],
        "top_items": [row.to_dict() for row in top_items],
        "timeline": timeline,
    }


def list_analysis_items(
    *,
    user_id: int,
    run_id: str,
    page: int,
    per_page: int,
    status: str | None = None,
    sort: str = "downloads",
) -> tuple[list[dict[str, Any]], int]:
    """Return paginated item projections after checking run ownership."""

    run = get_analysis_run(user_id=user_id, run_id=run_id)
    if run is None:
        return [], 0

    query = db.session.query(ZsxqAnalysisItem).filter(
        ZsxqAnalysisItem.run_id == run.id
    )
    if status:
        normalized_status = str(status).strip().lower()
        if normalized_status not in SUPPORTED_STATUSES:
            raise ZsxqAnalysisValidationError("status 参数不受支持")
        query = query.filter(ZsxqAnalysisItem.status == normalized_status)

    total = int(query.order_by(None).count())
    if sort == "latest":
        query = query.order_by(
            ZsxqAnalysisItem.upload_time.desc().nullslast(),
            ZsxqAnalysisItem.item_index.asc(),
        )
    elif sort == "index":
        query = query.order_by(ZsxqAnalysisItem.item_index.asc())
    elif sort == "downloads":
        query = query.order_by(
            ZsxqAnalysisItem.download_count.desc(),
            ZsxqAnalysisItem.item_index.asc(),
        )
    else:
        raise ZsxqAnalysisValidationError("sort 只能是 downloads、latest 或 index")

    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return [row.to_dict() for row in rows], total


def resolve_source_artifact(run: ZsxqAnalysisRun) -> Path:
    """Resolve a stored artifact while rejecting path traversal."""

    root = _artifact_root().resolve()
    relative = Path(run.source_artifact_path or "")
    if relative.is_absolute() or ".." in relative.parts:
        raise FileNotFoundError("invalid source artifact path")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise FileNotFoundError("invalid source artifact path") from exc
    return target


def _build_summary(
    items: Iterable[dict[str, Any]], duplicate_file_id_count: int
) -> dict[str, Any]:
    rows = list(items)
    downloads = [int(row["download_count"]) for row in rows]
    durations = [int(row["duration_seconds"]) for row in rows]
    upload_times = [
        row["upload_time"] for row in rows if row.get("upload_time")
    ]
    status_counts = {status: 0 for status in SUPPORTED_STATUSES}
    for row in rows:
        status_counts[row["status"]] += 1

    return {
        "item_count": len(rows),
        "done_count": status_counts["done"],
        "pending_count": status_counts["pending"],
        "failed_count": status_counts["failed"],
        "processing_count": status_counts["processing"],
        "total_download_count": sum(downloads),
        "download_count_min": min(downloads) if downloads else 0,
        "download_count_max": max(downloads) if downloads else 0,
        "download_count_avg": float(mean(downloads)) if downloads else 0,
        "download_count_median": float(median(downloads)) if downloads else 0,
        "total_file_size_bytes": sum(
            int(row["file_size_bytes"]) for row in rows
        ),
        "total_duration_seconds": sum(durations),
        "duration_avg_seconds": float(mean(durations)) if durations else 0,
        "duration_median_seconds": float(median(durations))
        if durations
        else 0,
        "duration_nonzero_count": sum(
            1 for duration in durations if duration > 0
        ),
        "unique_topic_count": len(
            {row["topic_id"] for row in rows if row.get("topic_id")}
        ),
        "duplicate_file_id_count": duplicate_file_id_count,
        "period_start": min(upload_times) if upload_times else None,
        "period_end": max(upload_times) if upload_times else None,
    }


def _build_topic_projections(
    items: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        topic_id = item.get("topic_id")
        if topic_id:
            grouped[topic_id].append(item)

    ordered = sorted(
        grouped.items(),
        key=lambda pair: (
            -len(pair[1]),
            -sum(row["download_count"] for row in pair[1]),
            pair[0],
        ),
    )
    result = []
    for rank, (topic_id, rows) in enumerate(ordered, start=1):
        representative = max(
            rows,
            key=lambda row: (
                row["download_count"],
                row.get("upload_time") or datetime.min,
                -row["item_index"],
            ),
        )
        upload_times = [
            row["upload_time"] for row in rows if row.get("upload_time")
        ]
        result.append(
            {
                "topic_id": topic_id,
                "rank": rank,
                "item_count": len(rows),
                "done_count": sum(row["status"] == "done" for row in rows),
                "total_download_count": sum(
                    row["download_count"] for row in rows
                ),
                "max_download_count": max(
                    row["download_count"] for row in rows
                ),
                "latest_upload_time": max(upload_times)
                if upload_times
                else None,
                "representative_name": representative["name"],
            }
        )
    return result


def _build_keyword_projections(
    items: Iterable[dict[str, Any]], kind: str
) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for item in items:
        candidates = _keyword_candidates(item["normalized_name"])
        for keyword, category in candidates.items():
            bucket = stats.setdefault(
                keyword,
                {
                    "kind": kind,
                    "keyword": keyword,
                    "category": category,
                    "item_count": 0,
                    "total_download_count": 0,
                },
            )
            # Known vocabulary wins if a generic n-gram found the same text.
            if bucket["category"] == "generic" and category != "generic":
                bucket["category"] = category
            bucket["item_count"] += 1
            bucket["total_download_count"] += item["download_count"]

    filtered = [
        bucket
        for bucket in stats.values()
        if bucket["category"] != "generic" or bucket["item_count"] >= 2
    ]
    filtered.sort(
        key=lambda bucket: (
            -bucket["item_count"],
            -bucket["total_download_count"],
            bucket["keyword"],
        )
    )
    result = []
    for rank, bucket in enumerate(filtered[:MAX_KEYWORDS], start=1):
        result.append(
            {
                "kind": kind,
                "keyword": bucket["keyword"],
                "category": bucket["category"],
                "rank": rank,
                "item_count": bucket["item_count"],
                "total_download_count": bucket["total_download_count"],
            }
        )
    return result


def _keyword_candidates(value: str) -> dict[str, str]:
    text = value or ""
    candidates: dict[str, str] = {}
    lowered = text.casefold()
    known_terms = {keyword for keyword, _category in _KNOWN_KEYWORDS}
    for keyword, category in _KNOWN_KEYWORDS:
        if keyword.casefold() in lowered:
            candidates[keyword] = category

    for ticker in _TICKER_RE.findall(text):
        candidates[ticker.upper()] = "ticker"

    for token in _LATIN_TOKEN_RE.findall(text):
        normalized = token.strip("._+-").upper()
        if len(normalized) >= 2 and normalized not in _LATIN_STOPWORDS:
            candidates.setdefault(normalized, "latin")

    if _jieba is not None:
        tokens = _jieba.lcut(text, cut_all=False)
        for token in tokens:
            token = token.strip(" -_·,，。:：()（）[]【】")
            if (
                len(token) < 2
                or token in _KEYWORD_STOPWORDS
                or token in known_terms
            ):
                continue
            if token.isdigit() or not _CJK_RUN_RE.search(token):
                continue
            candidates.setdefault(token, "generic")
    else:
        # Keep the fallback conservative: a noisy n-gram cloud is worse than
        # a smaller cloud containing only short, explainable filename terms.
        for run in _CJK_RUN_RE.findall(text):
            if (
                len(run) <= 4
                and run not in _KEYWORD_STOPWORDS
                and run not in known_terms
            ):
                candidates.setdefault(run, "generic")
    return candidates


def _normalize_filename(value: str) -> str:
    normalized = " ".join(str(value or "").replace("\x00", "").split())
    normalized = _EXTENSION_RE.sub("", normalized)
    normalized = _DUPLICATE_SUFFIX_RE.sub("", normalized)
    normalized = _DATE_SUFFIX_RE.sub("", normalized)
    return normalized.strip(" -_·") or "未命名文件"


def _text(value: Any, field: str, *, max_length: int) -> str:
    if value is None or isinstance(value, bool):
        raise ZsxqAnalysisValidationError(f"{field} 不能为空")
    text = str(value).replace("\x00", "").strip()
    if not text:
        raise ZsxqAnalysisValidationError(f"{field} 不能为空")
    if len(text) > max_length:
        raise ZsxqAnalysisValidationError(f"{field} 过长")
    return text


def _optional_text(value: Any, *, max_length: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ZsxqAnalysisValidationError("文本字段格式错误")
    text = str(value).replace("\x00", "").strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ZsxqAnalysisValidationError("文本字段过长")
    return text


def _non_negative_int(value: Any, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ZsxqAnalysisValidationError(f"{field} 必须是非负整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ZsxqAnalysisValidationError(f"{field} 必须是非负整数") from exc
    if number < 0 or (isinstance(value, float) and number != value):
        raise ZsxqAnalysisValidationError(f"{field} 必须是非负整数")
    return number


def _parse_datetime(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ZsxqAnalysisValidationError(f"{field} 必须是 ISO 8601 时间") from exc
    # Store the source-local wall clock as a naive datetime, matching the
    # project's existing timestamp convention and preserving date buckets.
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _display_filename(value: str | None) -> str:
    name = (
        os.path.basename(str(value or "analysis.json"))
        .replace("\x00", "")
        .strip()
    )
    name = name.replace("\\", "_").replace("/", "_")
    return (name or "analysis.json")[:MAX_SOURCE_FILENAME_LENGTH]


def _history_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "source_filename": row.source_filename,
        "status": row.status,
        "analysis_version": row.analysis_version,
        "source_sha256_prefix": (row.source_sha256 or "")[:12],
        "source_size_bytes": row.source_size_bytes or 0,
        "item_count": row.item_count or 0,
        "done_count": row.done_count or 0,
        "pending_count": row.pending_count or 0,
        "failed_count": row.failed_count or 0,
        "processing_count": row.processing_count or 0,
        "total_download_count": row.total_download_count or 0,
        "total_duration_seconds": row.total_duration_seconds or 0,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat()
        if row.completed_at
        else None,
    }


def _build_timeline(run_id: str) -> list[dict[str, Any]]:
    rows = (
        db.session.query(
            ZsxqAnalysisItem.upload_time,
            ZsxqAnalysisItem.download_count,
            ZsxqAnalysisItem.duration_seconds,
        )
        .filter(
            ZsxqAnalysisItem.run_id == run_id,
            ZsxqAnalysisItem.upload_time.isnot(None),
        )
        .all()
    )
    buckets: dict[str, dict[str, int]] = {}
    for upload_time, download_count, duration_seconds in rows:
        day = upload_time.date().isoformat()
        bucket = buckets.setdefault(
            day,
            {
                "date": day,
                "item_count": 0,
                "download_count": 0,
                "duration_seconds": 0,
            },
        )
        bucket["item_count"] += 1
        bucket["download_count"] += int(download_count or 0)
        bucket["duration_seconds"] += int(duration_seconds or 0)
    return [buckets[key] for key in sorted(buckets)]


def _build_duration_distribution(run_id: str) -> list[dict[str, Any]]:
    durations = [
        int(row.duration_seconds or 0)
        for row in db.session.query(ZsxqAnalysisItem.duration_seconds)
        .filter(ZsxqAnalysisItem.run_id == run_id)
        .all()
    ]
    return [
        {
            "key": "unknown",
            "label": "未知时长",
            "count": sum(duration <= 0 for duration in durations),
        },
        {
            "key": "short",
            "label": "短音频（少于 15 分钟）",
            "count": sum(0 < duration < 15 * 60 for duration in durations),
        },
        {
            "key": "medium",
            "label": "中等音频（15-45 分钟）",
            "count": sum(
                15 * 60 <= duration < 45 * 60 for duration in durations
            ),
        },
        {
            "key": "long",
            "label": "长音频（至少 45 分钟）",
            "count": sum(duration >= 45 * 60 for duration in durations),
        },
    ]


def _validate_kind_filter(kind: str | None) -> str:
    normalized = str(kind).strip().lower()
    if normalized not in SUPPORTED_KINDS:
        raise ZsxqAnalysisValidationError("kind 只能是 pdf 或 audio")
    return normalized


def _artifact_root() -> Path:
    configured = current_app.config.get("ZSXQ_ANALYSIS_ARTIFACT_ROOT")
    root = Path(configured) if configured else DEFAULT_ARTIFACT_ROOT
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root


def _write_source_artifact(
    *, user_id: int, run_id: str, source_bytes: bytes
) -> tuple[Path, str]:
    root = _artifact_root().resolve()
    relative = Path("users") / str(int(user_id)) / f"{run_id}.json"
    target = (root / relative).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{run_id}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(source_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    return target, relative.as_posix()


def _remove_artifact(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # The DB rollback is the source of truth; cleanup is best effort.
        pass
