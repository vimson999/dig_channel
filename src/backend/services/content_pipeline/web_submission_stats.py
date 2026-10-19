"""Build file-level publication statistics for browser content submissions."""

import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
UNIDENTIFIED = "未识别"
DOMESTIC_INSTITUTIONS = "内资机构"

_BANK_ALIASES = {
    "摩根士丹利": ("摩根士丹利", "Morgan Stanley", "大摩"),
    "摩根大通": ("摩根大通", "J.P. Morgan", "JP Morgan", "JPMorgan", "小摩"),
    "美银证券": ("美银证券", "美国银行", "Bank of America", "BofA", "美银"),
    "伯恩斯坦": ("伯恩斯坦", "博恩斯坦", "Bernstein", "盛博"),
    "中信建投": ("中信建投",),
    "中信证券": ("中信证券",),
    "华泰证券": ("华泰证券", "华泰"),
    "国泰海通": ("国泰海通", "国泰君安"),
    "招商证券": ("招商证券",),
    "广发证券": ("广发证券",),
    "申万宏源": ("申万宏源",),
    "兴业证券": ("兴业证券",),
    "国盛证券": ("国盛证券",),
    "国金证券": ("国金证券",),
    "东吴证券": ("东吴证券",),
    "中银证券": ("中银证券",),
    "东方证券": ("东方证券",),
    "银河证券": ("银河证券",),
    "天风证券": ("天风证券", "天风"),
    "高盛": ("Goldman Sachs", "Goldman", "高盛"),
    "瑞银": ("UBS", "瑞银"),
    "野村": ("Nomura", "野村"),
    "巴克莱": ("Barclays", "巴克莱"),
    "汇丰": ("HSBC", "汇丰"),
    "花旗": ("Citigroup", "Citi", "花旗"),
    "中金": ("CICC", "中金公司", "中金"),
    "杰富瑞": ("Jefferies", "杰富瑞"),
}
_ORDERED_BANK_ALIASES = sorted(
    ((alias, bank) for bank, aliases in _BANK_ALIASES.items() for alias in aliases),
    key=lambda item: len(item[0]),
    reverse=True,
)
_TICKER_PREFIX = re.compile(
    r"^\s*(?P<name>[^（(]{1,100}?)\s*[（(]\s*"
    r"(?P<ticker>(?:[A-Za-z]{1,8}|[0-9]{4,6})(?:[.-][A-Za-z0-9]{1,8})?)\s*[）)]"
)
_LEADING_SEPARATORS = " \t-_—–·:：|"
_LEADING_FILENAME_DATE = re.compile(
    r"^(?:\d{6}|\d{8}|\d{4}[-_.]\d{2}[-_.]\d{2})[-_—– ]+"
)


def publication_statistics_result_column(dialect_name, *, include_details=False):
    """Project file statistics before transferring large PostgreSQL audit JSON.

    The scalar expression is correlated with the outer publication_tasks row.
    Flattening rounds retains their order and the collector's retry semantics.
    """
    if dialect_name != "postgresql":
        from models.task import PublicationTask

        return PublicationTask.result

    from sqlalchemy import literal_column
    from sqlalchemy.dialects.postgresql import JSONB

    return literal_column(
        """(
            SELECT jsonb_build_object('rules', COALESCE(jsonb_agg(
                jsonb_strip_nulls(jsonb_build_object(
                    'file_type', stats_rule.value->'file_type',
                    /* detail_rule */
                    'verified_count', stats_rule.value->'verified_count',
                    'verification', jsonb_build_object(
                        'confirmed', stats_rule.value#>'{verification,confirmed}'
                    ),
                    'files', (
                        SELECT COALESCE(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                            /* detail_file */
                            'status', stats_file.value->'status',
                            'name', stats_file.value->'name',
                            'filename', stats_file.value->'filename',
                            'path', stats_file.value->'path',
                            'investment_bank', stats_file.value->'investment_bank',
                            'bank', stats_file.value->'bank',
                            'institution', stats_file.value->'institution',
                            'institution_name', stats_file.value->'institution_name'
                        )) ORDER BY stats_file.position), '[]'::jsonb)
                        FROM jsonb_array_elements(CASE
                            WHEN jsonb_typeof(stats_rule.value->'files') = 'array'
                            THEN stats_rule.value->'files' ELSE '[]'::jsonb
                        END) WITH ORDINALITY AS stats_file(value, position)
                        WHERE jsonb_typeof(stats_file.value) = 'object'
                    )
                )) ORDER BY stats_round.position, stats_rule.position
            ), '[]'::jsonb))
            FROM (SELECT CASE
                WHEN jsonb_typeof(publication_tasks.result::jsonb->'publish_result') = 'object'
                THEN publication_tasks.result::jsonb->'publish_result'
                ELSE publication_tasks.result::jsonb
            END AS payload) AS stats_payload
            CROSS JOIN LATERAL jsonb_array_elements(CASE
                WHEN jsonb_typeof(stats_payload.payload->'rounds') = 'array'
                THEN stats_payload.payload->'rounds'
                ELSE jsonb_build_array(stats_payload.payload)
            END) WITH ORDINALITY AS stats_round(value, position)
            CROSS JOIN LATERAL jsonb_array_elements(CASE
                WHEN jsonb_typeof(stats_round.value->'rules') = 'array'
                THEN stats_round.value->'rules' ELSE '[]'::jsonb
            END) WITH ORDINALITY AS stats_rule(value, position)
            WHERE jsonb_typeof(stats_rule.value) = 'object'
        )""".replace(
            "/* detail_rule */",
            "'topic_id', stats_rule.value->'topic_id'," if include_details else "",
        ).replace(
            "/* detail_file */",
            "'topic_id', stats_file.value->'topic_id'," if include_details else "",
        ),
        type_=JSONB,
    ).label("result")


def classify_publication_file(name, *, investment_bank=None, institution=None):
    """Infer names only from explicit metadata or recognizable filename markers."""
    filename = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = PurePosixPath(filename).stem
    bank = normalize_publication_bank(investment_bank) or _bank_from_filename(stem)
    institution_name = _clean_label(institution) or _institution_from_filename(stem)
    return bank or UNIDENTIFIED, institution_name or UNIDENTIFIED


def _bank_from_filename(stem):
    stem = _filename_without_leading_date(stem)
    lowered = stem.casefold()
    for alias, bank in _ORDERED_BANK_ALIASES:
        if lowered.startswith(alias.casefold()):
            return bank
    return ""


def _institution_from_filename(stem):
    stem = _filename_without_leading_date(stem)
    subject = stem
    lowered = stem.casefold()
    for alias, _bank in _ORDERED_BANK_ALIASES:
        if lowered.startswith(alias.casefold()):
            subject = stem[len(alias):].lstrip(_LEADING_SEPARATORS)
            break
    match = _TICKER_PREFIX.match(subject)
    if not match:
        return ""
    return _clean_label(match.group("name").strip(_LEADING_SEPARATORS + "，,。.;；:："))


def _filename_without_leading_date(stem):
    return _LEADING_FILENAME_DATE.sub("", str(stem or ""), count=1)


def _clean_label(value):
    text = str(value or "").strip()
    return text if text and text.casefold() not in {"unknown", "n/a", "null"} else ""


def normalize_publication_bank(value):
    label = _clean_label(value)
    for alias, bank in _ORDERED_BANK_ALIASES:
        if label.casefold() == alias.casefold():
            return bank
    return label


def _recorded_topic_url(item, rule):
    topic_id = str(item.get("topic_id") or rule.get("topic_id") or "").strip()
    if re.fullmatch(r"[0-9]{1,32}", topic_id):
        return f"https://wx.zsxq.com/dweb2/index/topic_detail/{topic_id}"
    return ""


def collect_verified_publication_files(publish_tasks, task_kinds, *, include_details=False):
    """Return verified files only, deduplicated across retries of the same clean task."""
    rows = []
    seen_files = set()
    for task in publish_tasks:
        task_id = str(getattr(task, "source_task_id", "") or "")
        parent_kind = task_kinds.get(task_id)
        if parent_kind not in {"pdf", "mp3"}:
            continue
        result = getattr(task, "result", None)
        result = result if isinstance(result, dict) else {}
        payload = result.get("publish_result") if isinstance(result.get("publish_result"), dict) else result
        rounds = payload.get("rounds") if isinstance(payload, dict) else None
        if not isinstance(rounds, list):
            rounds = [payload] if isinstance(payload, dict) else []

        for run in rounds:
            if not isinstance(run, dict):
                continue
            rules = run.get("rules") if isinstance(run.get("rules"), list) else []
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                kind = _kind_for_file_type(rule.get("file_type")) or parent_kind
                verified_count = _non_negative_int(rule.get("verified_count"))
                files = rule.get("files") if isinstance(rule.get("files"), list) else []
                verified_files = [
                    item for item in files
                    if isinstance(item, dict)
                    and str(item.get("status") or "").strip().lower()
                    in {"uploaded", "published", "verified", "success", "succeeded"}
                ]
                verification = rule.get("verification")
                if not verified_count and isinstance(verification, dict) and verification.get("confirmed"):
                    verified_count = len(verified_files)
                if verified_count <= 0:
                    continue

                completed_at = (
                    getattr(task, "completed_at", None)
                    or getattr(task, "updated_at", None)
                    or getattr(task, "created_at", None)
                )
                if not completed_at:
                    continue
                published_at = _to_shanghai_datetime(completed_at)
                selected_files = verified_files[:verified_count]
                for item in selected_files:
                    filename = item.get("name") or item.get("filename") or item.get("path") or ""
                    file_key = str(item.get("path") or filename).strip()
                    dedupe_key = (task_id, kind, file_key)
                    if not file_key or dedupe_key in seen_files:
                        continue
                    seen_files.add(dedupe_key)
                    bank, institution_name = classify_publication_file(
                        filename,
                        investment_bank=item.get("investment_bank") or item.get("bank"),
                        institution=item.get("institution") or item.get("institution_name"),
                    )
                    row = {
                        "published_at": published_at,
                        "kind": kind,
                        "investment_bank": DOMESTIC_INSTITUTIONS if kind == "mp3" else bank,
                        "institution": institution_name,
                        "count": 1,
                    }
                    if include_details:
                        row.update({
                            "filename": str(filename).replace("\\", "/").rsplit("/", 1)[-1],
                            "publish_task_id": str(getattr(task, "id", "") or ""),
                            "publish_url": _recorded_topic_url(item, rule),
                        })
                    rows.append(row)

                # Older/partial task payloads may have aggregate verified counts
                # without per-file details; preserve the count without guessing names.
                unattributed = max(0, verified_count - len(selected_files))
                if unattributed:
                    row = {
                        "published_at": published_at,
                        "kind": kind,
                        "investment_bank": DOMESTIC_INSTITUTIONS if kind == "mp3" else UNIDENTIFIED,
                        "institution": UNIDENTIFIED,
                        "count": unattributed,
                    }
                    if include_details:
                        row.update({
                            "filename": "",
                            "publish_task_id": str(getattr(task, "id", "") or ""),
                            "publish_url": _recorded_topic_url({}, rule),
                        })
                    rows.append(row)
    return rows


def paginate_publication_files(files, *, bank, start_date, end_date, kind="", page=1, page_size=25):
    selected = [
        item for item in files
        if item["investment_bank"] == bank
        and start_date <= item["published_at"].date() <= end_date
        and (not kind or item["kind"] == kind)
    ]
    selected.sort(key=lambda item: (
        -item["published_at"].timestamp(), item.get("filename", ""), item.get("publish_task_id", ""),
    ))
    totals = {"pdf": 0, "mp3": 0, "total": 0}
    for item in selected:
        totals[item["kind"]] += item["count"]
        totals["total"] += item["count"]
    total_pages = max(1, (len(selected) + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size
    items = [
        {
            "filename": item.get("filename", ""),
            "published_at": item["published_at"].isoformat(),
            "kind": item["kind"],
            "count": item["count"],
            "publish_url": item.get("publish_url", ""),
            "publish_task_id": item.get("publish_task_id", ""),
        }
        for item in selected[offset:offset + page_size]
    ]
    return {
        "investment_bank": bank, "from": start_date.isoformat(), "to": end_date.isoformat(),
        "kind": kind, "totals": totals, "items": items, "page": page, "page_size": page_size,
        "total_items": len(selected), "total_pages": total_pages, "has_more": page < total_pages,
    }


def summarize_publication_files(
    files,
    *,
    period,
    start_date,
    end_date,
    bank_filter="",
):
    in_range = [
        item for item in files
        if start_date <= item["published_at"].date() <= end_date
    ]
    banks = sorted({item["investment_bank"] for item in in_range})
    selected = [
        item for item in in_range
        if not bank_filter or item["investment_bank"] == bank_filter
    ]

    totals = {"pdf": 0, "mp3": 0, "total": 0}
    grouped = defaultdict(lambda: {"pdf": 0, "mp3": 0, "total": 0})
    for item in selected:
        key = (
            _period_label(item["published_at"].date(), period),
            item["investment_bank"],
        )
        count = _non_negative_int(item.get("count", 1))
        grouped[key][item["kind"]] += count
        grouped[key]["total"] += count
        totals[item["kind"]] += count
        totals["total"] += count

    rows = [
        {
            "period": key[0],
            "investment_bank": key[1],
            **counts,
        }
        for key, counts in grouped.items()
    ]
    rows.sort(key=lambda item: item["investment_bank"])
    rows.sort(key=lambda item: item["period"], reverse=True)
    return {
        "period": period,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "totals": totals,
        "filters": {"banks": banks},
        "rows": rows,
    }


def _period_label(value, period):
    if period == "day":
        return value.isoformat()
    if period == "week":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "month":
        return value.strftime("%Y-%m")
    return value.strftime("%Y")


def _kind_for_file_type(value):
    normalized = str(value or "").strip().lower()
    if normalized == "pdf":
        return "pdf"
    if normalized in {"mp3", "audio"}:
        return "mp3"
    return ""


def _non_negative_int(value):
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _to_shanghai_datetime(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI)
