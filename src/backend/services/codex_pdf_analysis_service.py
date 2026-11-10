"""Persist and read structured Codex PDF analysis reports."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

from sqlalchemy.exc import IntegrityError

from models import (
    CodexPdfAnalysisCandidate,
    CodexPdfAnalysisCandidateSource,
    CodexPdfAnalysisRanking,
    CodexPdfAnalysisRun,
    CodexPdfAnalysisSection,
    CodexPdfAnalysisSectionSource,
    CodexPdfAnalysisSource,
    CodexPdfAnalysisTarget,
    CodexPdfAnalysisTopic,
    User,
    db,
)


SUBMISSION_SCHEMA = "codex-pdf-analysis/v1"
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
MAX_SOURCE_RECORD_COUNT = 10000
MAX_SOURCES = 1000
MAX_SECTIONS = 20
MAX_TOPICS = 300
MAX_RANKINGS = 1500
MAX_TARGETS = 300
MAX_CANDIDATES = 100
MAX_LINKS = 5000

SECTION_CODES = frozenset(
    {
        "overview",
        "changes",
        "market_heat",
        "global_cross_market",
        "commodities_daily",
        "china_personal",
        "company_industry",
        "candidate_pool",
        "primary_recommendation",
        "data_quality",
    }
)
SECTION_ORDINALS = {
    "overview": 1,
    "changes": 2,
    "market_heat": 3,
    "global_cross_market": 4,
    "commodities_daily": 5,
    "china_personal": 6,
    "company_industry": 7,
    "candidate_pool": 8,
    "primary_recommendation": 9,
    "data_quality": 10,
}
TRACK_CODES = frozenset(
    {"china_personal", "global_cross_asset", "company_industry", "commodities"}
)
SOURCE_ROLES = frozenset(
    {"primary_report", "mechanism_support", "counterpoint", "background", "data_validation"}
)
RANKING_TYPES = frozenset(
    {
        "single_pdf_top",
        "topic_single_pdf",
        "special_topic_pdf",
        "commodity_recent_3d",
        "commodity_today",
        "commodity_download_gain",
        "target_pdf",
        "candidate_source",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class CodexPdfAnalysisValidationError(ValueError):
    """The submitted report does not match the typed persistence contract."""


class CodexPdfAnalysisDuplicateError(ValueError):
    """An identical source and analysis version already exists."""

    def __init__(self, existing_run_id: str):
        self.existing_run_id = existing_run_id
        super().__init__(f"该来源和分析版本已保存，run_id={existing_run_id}")


def _object(value: Any, label: str, allowed: set[str], required: set[str] | None = None):
    if not isinstance(value, dict):
        raise CodexPdfAnalysisValidationError(f"{label} 必须是对象")
    required = required or allowed
    missing = required - value.keys()
    unknown = value.keys() - allowed
    if missing:
        raise CodexPdfAnalysisValidationError(f"{label} 缺少字段：{', '.join(sorted(missing))}")
    if unknown:
        raise CodexPdfAnalysisValidationError(f"{label} 不支持字段：{', '.join(sorted(unknown))}")
    return value


def _text(value: Any, label: str, max_length: int, *, required: bool = True):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise CodexPdfAnalysisValidationError(f"{label} 必须是文本")
    if "\x00" in value:
        raise CodexPdfAnalysisValidationError(f"{label} 含有无效字符")
    if required and not value.strip():
        raise CodexPdfAnalysisValidationError(f"{label} 不能为空")
    if len(value) > max_length:
        raise CodexPdfAnalysisValidationError(f"{label} 不能超过 {max_length} 个字符")
    return value


def _integer(value: Any, label: str, *, minimum: int = 0, maximum: int | None = None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodexPdfAnalysisValidationError(f"{label} 必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise CodexPdfAnalysisValidationError(f"{label} 不能小于 {minimum}")
        raise CodexPdfAnalysisValidationError(f"{label} 必须在 {minimum}-{maximum} 之间")
    return value


def _number(value: Any, label: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CodexPdfAnalysisValidationError(f"{label} 必须是数字")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise CodexPdfAnalysisValidationError(f"{label} 必须是有限的非负数")
    return number


def _rows(value: Any, label: str, maximum: int):
    if not isinstance(value, list):
        raise CodexPdfAnalysisValidationError(f"{label} 必须是数组")
    if len(value) > maximum:
        raise CodexPdfAnalysisValidationError(f"{label} 最多允许 {maximum} 条")
    return value


def _date(value: Any, label: str):
    if value is None or value == "":
        return None
    value = _text(value, label, 64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CodexPdfAnalysisValidationError(f"{label} 必须是 ISO 8601 时间") from exc
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def validate_submission(submission: Any) -> dict[str, Any]:
    """Validate shape, limits, enumerations, and all within-run references."""

    top_fields = {
        "schema", "contract_version", "analysis_version", "source",
        "overall_summary", "selected_candidate_title", "data_quality_note",
        "sources", "sections", "topics", "rankings", "targets", "candidates",
        "candidate_sources", "section_sources",
    }
    _object(submission, "payload", top_fields)
    if submission["schema"] != SUBMISSION_SCHEMA:
        raise CodexPdfAnalysisValidationError(f"schema 必须是 {SUBMISSION_SCHEMA}")

    source_data = _object(
        submission["source"],
        "source",
        {"filename", "exported_at", "sha256", "record_count", "total_download_count"},
    )
    sha256 = _text(source_data["sha256"], "source.sha256", 64)
    if not _SHA256_RE.fullmatch(sha256):
        raise CodexPdfAnalysisValidationError("source.sha256 必须是 64 位十六进制 SHA-256")

    normalized: dict[str, Any] = {
        "contract_version": _text(submission["contract_version"], "contract_version", 64),
        "analysis_version": _text(submission["analysis_version"], "analysis_version", 64),
        "source": {
            "filename": _text(source_data["filename"], "source.filename", 255),
            "exported_at": _date(source_data["exported_at"], "source.exported_at"),
            # The raw JSON is not uploaded, so this is retained as caller-provided provenance.
            "sha256": sha256.lower(),
            "record_count": _integer(
                source_data["record_count"], "source.record_count", maximum=MAX_SOURCE_RECORD_COUNT
            ),
            "total_download_count": _integer(
                source_data["total_download_count"], "source.total_download_count"
            ),
        },
        "overall_summary": _text(submission["overall_summary"], "overall_summary", 20000),
        "selected_candidate_title": _text(
            submission["selected_candidate_title"], "selected_candidate_title", 500, required=False
        ),
        "data_quality_note": _text(
            submission["data_quality_note"], "data_quality_note", 40000, required=False
        ),
    }

    source_rows = _rows(submission["sources"], "sources", MAX_SOURCES)
    normalized["sources"] = []
    source_keys = set()
    for index, value in enumerate(source_rows, start=1):
        row = _object(
            value,
            f"sources[{index}]",
            {"source_key", "name", "institution", "report_date_text", "upload_time_text", "status", "download_count"},
        )
        item = {
            "source_key": _text(row["source_key"], f"sources[{index}].source_key", 160),
            "name": _text(row["name"], f"sources[{index}].name", 2000),
            "institution": _text(row["institution"], f"sources[{index}].institution", 255, required=False),
            "report_date_text": _text(row["report_date_text"], f"sources[{index}].report_date_text", 64, required=False),
            "upload_time_text": _text(row["upload_time_text"], f"sources[{index}].upload_time_text", 64, required=False),
            "status": _text(row["status"], f"sources[{index}].status", 64, required=False),
            "download_count": None if row["download_count"] is None else _integer(row["download_count"], f"sources[{index}].download_count"),
        }
        if item["source_key"] in source_keys:
            raise CodexPdfAnalysisValidationError(f"sources 中 source_key 重复：{item['source_key']}")
        source_keys.add(item["source_key"])
        normalized["sources"].append(item)

    section_rows = _rows(submission["sections"], "sections", MAX_SECTIONS)
    normalized["sections"] = []
    section_codes = set()
    section_ordinals = set()
    for index, value in enumerate(section_rows, start=1):
        row = _object(
            value,
            f"sections[{index}]",
            {"module_code", "title", "ordinal", "summary_text", "body_text", "evidence_state"},
        )
        module_code = _text(row["module_code"], f"sections[{index}].module_code", 64)
        if module_code not in SECTION_CODES:
            raise CodexPdfAnalysisValidationError(f"sections[{index}].module_code 不受支持")
        if module_code in section_codes:
            raise CodexPdfAnalysisValidationError(f"sections 中 module_code 重复：{module_code}")
        section_codes.add(module_code)
        ordinal = _integer(row["ordinal"], f"sections[{index}].ordinal", minimum=1, maximum=MAX_SECTIONS)
        if ordinal != SECTION_ORDINALS[module_code]:
            raise CodexPdfAnalysisValidationError(
                f"sections[{index}].ordinal 必须为 {module_code} 对应的 {SECTION_ORDINALS[module_code]}"
            )
        if ordinal in section_ordinals:
            raise CodexPdfAnalysisValidationError(f"sections 中 ordinal 重复：{ordinal}")
        section_ordinals.add(ordinal)
        normalized["sections"].append(
            {
                "module_code": module_code,
                "title": _text(row["title"], f"sections[{index}].title", 255),
                "ordinal": ordinal,
                "summary_text": _text(row["summary_text"], f"sections[{index}].summary_text", 20000, required=False),
                "body_text": _text(row["body_text"], f"sections[{index}].body_text", 200000),
                "evidence_state": _text(row["evidence_state"], f"sections[{index}].evidence_state", 48, required=False),
            }
        )
    if section_codes != SECTION_CODES:
        missing = sorted(SECTION_CODES - section_codes)
        raise CodexPdfAnalysisValidationError(f"sections 必须覆盖固定交付模块，缺少：{', '.join(missing)}")

    topic_rows = _rows(submission["topics"], "topics", MAX_TOPICS)
    normalized["topics"] = []
    seen_topics = set()
    seen_topic_ranks = set()
    for index, value in enumerate(topic_rows, start=1):
        row = _object(
            value,
            f"topics[{index}]",
            {"topic_name", "classification_scope", "rank", "pdf_count", "total_download_count", "average_download_count", "institution_count", "keywords_text"},
        )
        topic_name = _text(row["topic_name"], f"topics[{index}].topic_name", 255)
        scope = _text(row["classification_scope"], f"topics[{index}].classification_scope", 16)
        if scope not in {"multi", "primary"}:
            raise CodexPdfAnalysisValidationError(f"topics[{index}].classification_scope 只能是 multi 或 primary")
        identity = (scope, topic_name)
        if identity in seen_topics:
            raise CodexPdfAnalysisValidationError(f"topics 中主题重复：{scope}/{topic_name}")
        seen_topics.add(identity)
        rank = _integer(row["rank"], f"topics[{index}].rank", minimum=1, maximum=MAX_TOPICS)
        if (scope, rank) in seen_topic_ranks:
            raise CodexPdfAnalysisValidationError(f"topics 中同一口径的 rank 重复：{scope}/{rank}")
        seen_topic_ranks.add((scope, rank))
        normalized["topics"].append(
            {
                "topic_name": topic_name,
                "classification_scope": scope,
                "rank": rank,
                "pdf_count": _integer(row["pdf_count"], f"topics[{index}].pdf_count", maximum=MAX_SOURCE_RECORD_COUNT),
                "total_download_count": _integer(row["total_download_count"], f"topics[{index}].total_download_count"),
                "average_download_count": _number(row["average_download_count"], f"topics[{index}].average_download_count"),
                "institution_count": _integer(row["institution_count"], f"topics[{index}].institution_count", maximum=MAX_SOURCE_RECORD_COUNT),
                "keywords_text": _text(row["keywords_text"], f"topics[{index}].keywords_text", 4000, required=False),
            }
        )

    ranking_rows = _rows(submission["rankings"], "rankings", MAX_RANKINGS)
    normalized["rankings"] = []
    seen_rankings = set()
    for index, value in enumerate(ranking_rows, start=1):
        row = _object(value, f"rankings[{index}]", {"ranking_type", "rank", "source_key", "topic_name"})
        ranking_type = _text(row["ranking_type"], f"rankings[{index}].ranking_type", 64)
        if ranking_type not in RANKING_TYPES:
            raise CodexPdfAnalysisValidationError(f"rankings[{index}].ranking_type 不受支持")
        topic_name = _text(row["topic_name"], f"rankings[{index}].topic_name", 255, required=False)
        rank = _integer(row["rank"], f"rankings[{index}].rank", minimum=1, maximum=MAX_RANKINGS)
        source_key = _text(row["source_key"], f"rankings[{index}].source_key", 160)
        if source_key not in source_keys:
            raise CodexPdfAnalysisValidationError(f"rankings[{index}] 引用了不存在的来源：{source_key}")
        identity = (ranking_type, topic_name or "", rank)
        if identity in seen_rankings:
            raise CodexPdfAnalysisValidationError(f"rankings 中位置重复：{identity}")
        seen_rankings.add(identity)
        normalized["rankings"].append(
            {"ranking_type": ranking_type, "rank": rank, "source_key": source_key, "topic_name": topic_name}
        )

    target_rows = _rows(submission["targets"], "targets", MAX_TARGETS)
    normalized["targets"] = []
    seen_targets = set()
    for index, value in enumerate(target_rows, start=1):
        row = _object(
            value,
            f"targets[{index}]",
            {"ticker", "display_name", "main_topic", "pdf_count", "total_download_count", "average_download_count", "institution_count", "institutions_text", "aggregation_note"},
        )
        ticker = _text(row["ticker"], f"targets[{index}].ticker", 32)
        if ticker in seen_targets:
            raise CodexPdfAnalysisValidationError(f"targets 中 ticker 重复：{ticker}")
        seen_targets.add(ticker)
        normalized["targets"].append(
            {
                "ticker": ticker,
                "display_name": _text(row["display_name"], f"targets[{index}].display_name", 255),
                "main_topic": _text(row["main_topic"], f"targets[{index}].main_topic", 255, required=False),
                "pdf_count": _integer(row["pdf_count"], f"targets[{index}].pdf_count", maximum=MAX_SOURCE_RECORD_COUNT),
                "total_download_count": _integer(row["total_download_count"], f"targets[{index}].total_download_count"),
                "average_download_count": _number(row["average_download_count"], f"targets[{index}].average_download_count"),
                "institution_count": _integer(row["institution_count"], f"targets[{index}].institution_count", maximum=MAX_SOURCE_RECORD_COUNT),
                "institutions_text": _text(row["institutions_text"], f"targets[{index}].institutions_text", 6000, required=False),
                "aggregation_note": _text(row["aggregation_note"], f"targets[{index}].aggregation_note", 12000, required=False),
            }
        )

    candidate_rows = _rows(submission["candidates"], "candidates", MAX_CANDIDATES)
    normalized["candidates"] = []
    candidate_keys = set()
    candidate_ordinals = set()
    primary_count = 0
    for index, value in enumerate(candidate_rows, start=1):
        row = _object(
            value,
            f"candidates[{index}]",
            {"candidate_key", "track_code", "title", "parent_question", "audience", "priority", "new_evidence", "rationale", "uncertainty_text", "is_primary", "ordinal"},
        )
        candidate_key = _text(row["candidate_key"], f"candidates[{index}].candidate_key", 80)
        track_code = _text(row["track_code"], f"candidates[{index}].track_code", 48)
        if candidate_key in candidate_keys:
            raise CodexPdfAnalysisValidationError(f"candidates 中 candidate_key 重复：{candidate_key}")
        if track_code not in TRACK_CODES:
            raise CodexPdfAnalysisValidationError(f"candidates[{index}].track_code 不受支持")
        if not isinstance(row["is_primary"], bool):
            raise CodexPdfAnalysisValidationError(f"candidates[{index}].is_primary 必须是布尔值")
        ordinal = _integer(row["ordinal"], f"candidates[{index}].ordinal", minimum=1, maximum=MAX_CANDIDATES)
        if ordinal in candidate_ordinals:
            raise CodexPdfAnalysisValidationError(f"candidates 中 ordinal 重复：{ordinal}")
        candidate_ordinals.add(ordinal)
        candidate_keys.add(candidate_key)
        primary_count += int(row["is_primary"])
        normalized["candidates"].append(
            {
                "candidate_key": candidate_key,
                "track_code": track_code,
                "title": _text(row["title"], f"candidates[{index}].title", 500),
                "parent_question": _text(row["parent_question"], f"candidates[{index}].parent_question", 12000),
                "audience": _text(row["audience"], f"candidates[{index}].audience", 6000, required=False),
                "priority": _integer(row["priority"], f"candidates[{index}].priority", minimum=1, maximum=5),
                "new_evidence": _text(row["new_evidence"], f"candidates[{index}].new_evidence", 12000, required=False),
                "rationale": _text(row["rationale"], f"candidates[{index}].rationale", 20000),
                "uncertainty_text": _text(row["uncertainty_text"], f"candidates[{index}].uncertainty_text", 12000, required=False),
                "is_primary": row["is_primary"],
                "ordinal": ordinal,
            }
        )
    if candidate_rows and primary_count != 1:
        raise CodexPdfAnalysisValidationError("candidates 必须且只能有一个 is_primary=true")

    candidate_source_rows = _rows(submission["candidate_sources"], "candidate_sources", MAX_LINKS)
    normalized["candidate_sources"] = []
    seen_candidate_sources = set()
    candidates_with_sources = set()
    for index, value in enumerate(candidate_source_rows, start=1):
        row = _object(
            value,
            f"candidate_sources[{index}]",
            {"candidate_key", "source_key", "source_role", "selection_reason"},
        )
        candidate_key = _text(row["candidate_key"], f"candidate_sources[{index}].candidate_key", 80)
        source_key = _text(row["source_key"], f"candidate_sources[{index}].source_key", 160)
        source_role = _text(row["source_role"], f"candidate_sources[{index}].source_role", 48)
        if candidate_key not in candidate_keys or source_key not in source_keys:
            raise CodexPdfAnalysisValidationError(f"candidate_sources[{index}] 引用了不存在的候选或来源")
        if source_role not in SOURCE_ROLES:
            raise CodexPdfAnalysisValidationError(f"candidate_sources[{index}].source_role 不受支持")
        identity = (candidate_key, source_key, source_role)
        if identity in seen_candidate_sources:
            raise CodexPdfAnalysisValidationError(f"candidate_sources 中关联重复：{identity}")
        seen_candidate_sources.add(identity)
        candidates_with_sources.add(candidate_key)
        normalized["candidate_sources"].append(
            {
                "candidate_key": candidate_key,
                "source_key": source_key,
                "source_role": source_role,
                "selection_reason": _text(row["selection_reason"], f"candidate_sources[{index}].selection_reason", 12000),
            }
        )

    section_source_rows = _rows(submission["section_sources"], "section_sources", MAX_LINKS)
    normalized["section_sources"] = []
    seen_section_sources = set()
    sections_with_sources = set()
    for index, value in enumerate(section_source_rows, start=1):
        row = _object(
            value,
            f"section_sources[{index}]",
            {"module_code", "source_key", "source_role", "evidence_note"},
        )
        module_code = _text(row["module_code"], f"section_sources[{index}].module_code", 64)
        source_key = _text(row["source_key"], f"section_sources[{index}].source_key", 160)
        source_role = _text(row["source_role"], f"section_sources[{index}].source_role", 48)
        if module_code not in section_codes or source_key not in source_keys:
            raise CodexPdfAnalysisValidationError(f"section_sources[{index}] 引用了不存在的模块或来源")
        if source_role not in SOURCE_ROLES:
            raise CodexPdfAnalysisValidationError(f"section_sources[{index}].source_role 不受支持")
        identity = (module_code, source_key, source_role)
        if identity in seen_section_sources:
            raise CodexPdfAnalysisValidationError(f"section_sources 中关联重复：{identity}")
        seen_section_sources.add(identity)
        sections_with_sources.add(module_code)
        normalized["section_sources"].append(
            {
                "module_code": module_code,
                "source_key": source_key,
                "source_role": source_role,
                "evidence_note": _text(row["evidence_note"], f"section_sources[{index}].evidence_note", 12000, required=False),
            }
        )

    if candidates_with_sources != candidate_keys:
        missing = sorted(candidate_keys - candidates_with_sources)
        raise CodexPdfAnalysisValidationError(f"每个候选至少要关联一份 PDF，缺少：{', '.join(missing)}")
    referenced_sources = (
        {row["source_key"] for row in normalized["rankings"]}
        | {row["source_key"] for row in normalized["candidate_sources"]}
        | {row["source_key"] for row in normalized["section_sources"]}
    )
    if referenced_sources != source_keys:
        unused = sorted(source_keys - referenced_sources)
        missing = sorted(referenced_sources - source_keys)
        raise CodexPdfAnalysisValidationError(
            f"sources 只能包含报告实际引用的 PDF；未引用={unused[:5]}，缺失={missing[:5]}"
        )
    if candidate_rows:
        primary = next(row for row in normalized["candidates"] if row["is_primary"])
        if normalized["selected_candidate_title"] != primary["title"]:
            raise CodexPdfAnalysisValidationError("selected_candidate_title 必须与主推候选标题一致")
    elif normalized["selected_candidate_title"]:
        raise CodexPdfAnalysisValidationError("没有候选时 selected_candidate_title 必须为空")

    return normalized


def resolve_active_admin_id(requested_user_id: int | None = None) -> int:
    """Resolve report ownership without requesting another login or token."""

    query = User.query.filter_by(is_active=True, is_admin=True).order_by(User.id.asc())
    if requested_user_id is not None:
        user = query.filter(User.id == requested_user_id).first()
        if user is None:
            raise CodexPdfAnalysisValidationError("指定用户不存在或不是有效管理员")
        return user.id
    users = query.limit(2).all()
    if len(users) != 1:
        raise CodexPdfAnalysisValidationError(
            "无法唯一确定 Shioa 管理员归属；请通过 --user-id 指定现有管理员 ID"
        )
    return users[0].id


def save_codex_pdf_analysis(user_id: int, submission: Any) -> CodexPdfAnalysisRun:
    """Write one completed analysis atomically; never store the input JSON."""

    data = validate_submission(submission)
    source_data = data["source"]
    duplicate = CodexPdfAnalysisRun.query.filter_by(
        user_id=user_id,
        source_sha256=source_data["sha256"],
        contract_version=data["contract_version"],
        analysis_version=data["analysis_version"],
    ).first()
    if duplicate is not None:
        raise CodexPdfAnalysisDuplicateError(duplicate.id)

    now = datetime.utcnow()
    run = CodexPdfAnalysisRun(
        user_id=user_id,
        source_filename=source_data["filename"],
        source_exported_at=source_data["exported_at"],
        source_sha256=source_data["sha256"],
        source_record_count=source_data["record_count"],
        source_total_download_count=source_data["total_download_count"],
        contract_version=data["contract_version"],
        analysis_version=data["analysis_version"],
        status="completed",
        created_at=now,
        completed_at=now,
        overall_summary=data["overall_summary"],
        selected_candidate_title=data["selected_candidate_title"],
        data_quality_note=data["data_quality_note"],
    )
    try:
        db.session.add(run)
        db.session.flush()

        source_by_key = {}
        source_rows = [
            CodexPdfAnalysisSource(run_id=run.id, **row)
            for row in data["sources"]
        ]
        db.session.add_all(source_rows)
        db.session.flush()
        for row in source_rows:
            source_by_key[row.source_key] = row

        section_by_code = {}
        section_rows = [
            CodexPdfAnalysisSection(run_id=run.id, **row)
            for row in data["sections"]
        ]
        db.session.add_all(section_rows)
        db.session.flush()
        for row in section_rows:
            section_by_code[row.module_code] = row

        db.session.add_all(
            CodexPdfAnalysisTopic(run_id=run.id, **row)
            for row in data["topics"]
        )
        db.session.add_all(
            CodexPdfAnalysisRanking(
                run_id=run.id,
                ranking_type=row["ranking_type"],
                rank=row["rank"],
                source_id=source_by_key[row["source_key"]].id,
                topic_name=row["topic_name"],
            )
            for row in data["rankings"]
        )
        db.session.add_all(
            CodexPdfAnalysisTarget(run_id=run.id, **row)
            for row in data["targets"]
        )

        candidate_by_key = {}
        candidate_rows = [
            CodexPdfAnalysisCandidate(run_id=run.id, **row)
            for row in data["candidates"]
        ]
        db.session.add_all(candidate_rows)
        db.session.flush()
        for row in candidate_rows:
            candidate_by_key[row.candidate_key] = row

        db.session.add_all(
            CodexPdfAnalysisCandidateSource(
                run_id=run.id,
                candidate_id=candidate_by_key[row["candidate_key"]].id,
                source_id=source_by_key[row["source_key"]].id,
                source_role=row["source_role"],
                selection_reason=row["selection_reason"],
            )
            for row in data["candidate_sources"]
        )
        db.session.add_all(
            CodexPdfAnalysisSectionSource(
                run_id=run.id,
                section_id=section_by_code[row["module_code"]].id,
                source_id=source_by_key[row["source_key"]].id,
                source_role=row["source_role"],
                evidence_note=row["evidence_note"],
            )
            for row in data["section_sources"]
        )
        db.session.commit()
        return run
    except IntegrityError as exc:
        db.session.rollback()
        duplicate = CodexPdfAnalysisRun.query.filter_by(
            user_id=user_id,
            source_sha256=source_data["sha256"],
            contract_version=data["contract_version"],
            analysis_version=data["analysis_version"],
        ).first()
        if duplicate is not None:
            raise CodexPdfAnalysisDuplicateError(duplicate.id) from exc
        raise
    except Exception:
        db.session.rollback()
        raise


def list_codex_pdf_analysis_runs(user_id: int, *, page: int, per_page: int):
    query = CodexPdfAnalysisRun.query.filter_by(user_id=user_id)
    total = query.count()
    runs = (
        query.order_by(CodexPdfAnalysisRun.created_at.desc(), CodexPdfAnalysisRun.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return [run.to_history_dict() for run in runs], total


def get_codex_pdf_analysis_detail(user_id: int, run_id: str):
    run = CodexPdfAnalysisRun.query.filter_by(user_id=user_id, id=run_id).first()
    if run is None:
        return None

    sources = CodexPdfAnalysisSource.query.filter_by(run_id=run.id).order_by(CodexPdfAnalysisSource.id).all()
    source_by_id = {row.id: row for row in sources}
    sections = CodexPdfAnalysisSection.query.filter_by(run_id=run.id).order_by(CodexPdfAnalysisSection.ordinal).all()
    section_by_id = {row.id: row for row in sections}
    candidates = CodexPdfAnalysisCandidate.query.filter_by(run_id=run.id).order_by(CodexPdfAnalysisCandidate.ordinal).all()
    candidate_by_id = {row.id: row for row in candidates}
    section_source_rows = CodexPdfAnalysisSectionSource.query.filter_by(run_id=run.id).order_by(CodexPdfAnalysisSectionSource.id).all()
    candidate_source_rows = CodexPdfAnalysisCandidateSource.query.filter_by(run_id=run.id).order_by(CodexPdfAnalysisCandidateSource.id).all()
    section_sources_by_id: dict[int, list[dict[str, Any]]] = {row.id: [] for row in sections}
    candidate_sources_by_id: dict[int, list[dict[str, Any]]] = {row.id: [] for row in candidates}
    for link in section_source_rows:
        source = source_by_id.get(link.source_id)
        if source is not None:
            section_sources_by_id[link.section_id].append(link.to_dict(source))
    for link in candidate_source_rows:
        source = source_by_id.get(link.source_id)
        if source is not None:
            candidate_sources_by_id[link.candidate_id].append(link.to_dict(source))

    rankings = CodexPdfAnalysisRanking.query.filter_by(run_id=run.id).order_by(
        CodexPdfAnalysisRanking.ranking_type,
        CodexPdfAnalysisRanking.topic_name,
        CodexPdfAnalysisRanking.rank,
    ).all()
    return {
        "run": run.to_detail_dict(),
        "sections": [
            {**row.to_dict(), "sources": section_sources_by_id[row.id]}
            for row in sections
        ],
        "sources": [row.to_dict() for row in sources],
        "topics": [
            row.to_dict()
            for row in CodexPdfAnalysisTopic.query.filter_by(run_id=run.id).order_by(
                CodexPdfAnalysisTopic.classification_scope,
                CodexPdfAnalysisTopic.rank,
            ).all()
        ],
        "rankings": [
            row.to_dict(source_by_id[row.source_id])
            for row in rankings
            if row.source_id in source_by_id
        ],
        "targets": [
            row.to_dict()
            for row in CodexPdfAnalysisTarget.query.filter_by(run_id=run.id).order_by(
                CodexPdfAnalysisTarget.total_download_count.desc(),
                CodexPdfAnalysisTarget.display_name,
            ).all()
        ],
        "candidates": [
            {**row.to_dict(), "sources": candidate_sources_by_id[row.id]}
            for row in candidates
        ],
    }
