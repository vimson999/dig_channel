import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests
from bilibili_api import Credential, user
from sqlalchemy import and_, or_
from sqlalchemy.orm import load_only

from models import db
from models.bilibili_intelligence import BilibiliCompetitor, BilibiliCompetitorVideo
from models.report_video import ReportVideoAnalysis, ReportVideoReport, ReportVideoTask
from models.task import PublicationTask
from models.zsxq_sync import ZsxqAttachment, ZsxqTopic
from services.platform_credential_service import (
    is_auth_error,
    mark_credential_error,
    mark_credential_success,
    resolve_bilibili_auth,
)
from services.runtime_guard import release_db_session_quietly


LOGGER = logging.getLogger("bilibili_intelligence")

CONTENT_TYPES = {"report", "audio"}
DECISION_STATUSES = {"pending", "worth_following", "ignored", "confirmed_match", "rejected_match"}
MATCH_BATCH_LIMIT = 10
DEFAULT_LOCAL_CANDIDATE_LIMIT = 500
MAX_LOCAL_CANDIDATE_LIMIT = 2000
DEFAULT_SECONDARY_CANDIDATE_LIMIT = 100
MAX_SECONDARY_CANDIDATE_LIMIT = 500

_REPORT_FEATURES = {
    "report_pdf_audio_workflow",
    "report_audio_workflow",
    "report_script_material_workflow",
}
_AUDIO_FEATURES = {"mp3_video_workflow"}
_AUDIO_EXTENSIONS = {"mp3", "m4a", "wav", "aac", "flac", "ogg"}

_ALIASES = {
    "摩根士丹利": ("大摩", "morganstanley", "morgan stanley"),
    "摩根大通": ("小摩", "jpmorgan", "jp morgan"),
    "美国银行": ("美银", "bankofamerica", "bank of america", "bofa"),
    "高盛": ("goldmansachs", "goldman sachs"),
    "瑞银": ("ubs",),
    "花旗": ("citigroup", "citi"),
    "野村": ("nomura",),
    "伯恩斯坦": ("bernstein",),
    "杰富瑞": ("jefferies",),
    "中金": ("cicc", "中金公司"),
}

_BOILERPLATE_RE = re.compile(
    r"(?:全网(?:最快|独家)|推荐程度|\d+(?:\.\d+)?星(?:推荐|选看|必看)|"
    r"机构|内资|外资|开门会|闭门会|电话会|会议纪要|研报|解读|视频|完整版|高清字幕版)",
    flags=re.I,
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[./_-]?(0?[1-9]|1[0-2])[./_-]?(0?[1-9]|[12]\d|3[01])(?!\d)")
_CANDIDATE_CACHE: Dict[str, Dict[str, Any]] = {}
_CANDIDATE_CACHE_LOCK = threading.RLock()


class BilibiliIntelligenceError(RuntimeError):
    pass


def _int_env(name: str, default: int, *, lower: int = 1, upper: int = 10000) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "--":
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _duration_seconds(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    parts = str(value or "").strip().split(":")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _published_at(value: Any) -> Optional[datetime]:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.utcfromtimestamp(timestamp) if timestamp > 0 else None


def _credential_from_auth(auth: Dict[str, Any]) -> Credential:
    return Credential(
        sessdata=auth.get("sessdata"),
        bili_jct=auth.get("bili_jct"),
        buvid3=auth.get("buvid3"),
        dedeuserid=auth.get("dedeuserid"),
    )


async def _fetch_user_videos(uid: str, credential: Credential, page_size: int) -> Dict[str, Any]:
    return await user.User(uid=int(uid), credential=credential).get_videos(
        pn=1,
        ps=page_size,
        order=user.VideoOrder.PUBDATE,
    )


def fetch_user_videos(uid: str, credential: Credential, page_size: int = 30) -> Dict[str, Any]:
    return asyncio.run(_fetch_user_videos(uid, credential, page_size))


def _video_values(item: Dict[str, Any], *, competitor_id: str, content_type: str) -> Dict[str, Any]:
    bvid = str(item.get("bvid") or "").strip()
    if not bvid:
        raise BilibiliIntelligenceError("B站投稿缺少 BV 号")
    return {
        "competitor_id": competitor_id,
        "content_type": content_type,
        "bvid": bvid,
        "aid": str(item.get("aid") or "")[:32] or None,
        "title": str(item.get("title") or bvid).strip()[:500],
        "description": str(item.get("description") or "").strip() or None,
        "cover_url": str(item.get("pic") or "").strip() or None,
        "duration_seconds": _duration_seconds(item.get("length")),
        "published_at": _published_at(item.get("created")),
        "play_count": _safe_int(item.get("play")),
        "comment_count": _safe_int(item.get("comment")),
        "danmaku_count": _safe_int(item.get("video_review")),
        "is_charging": bool(item.get("is_charging_arc") or item.get("is_pay")),
        "raw_payload": dict(item),
        "last_seen_at": datetime.utcnow(),
    }


def sync_competitor(competitor_id: str, *, page_size: int = 30) -> Dict[str, Any]:
    page_size = max(1, min(50, int(page_size or 30)))
    competitor = BilibiliCompetitor.query.filter(BilibiliCompetitor.id == str(competitor_id)).one_or_none()
    if not competitor:
        raise BilibiliIntelligenceError("竞争对手不存在")
    competitor_snapshot = {
        "id": competitor.id,
        "uid": competitor.uid,
        "name": competitor.name,
        "content_type": competitor.content_type,
    }

    auth = resolve_bilibili_auth(required=True)
    credential_id = auth.get("credential_id")
    credential = _credential_from_auth(auth)
    release_db_session_quietly()

    try:
        payload = fetch_user_videos(competitor_snapshot["uid"], credential, page_size=page_size)
    except Exception as exc:
        LOGGER.warning("Bilibili competitor sync failed: competitor=%s error=%s", competitor_id, exc)
        try:
            competitor = BilibiliCompetitor.query.filter(BilibiliCompetitor.id == competitor_snapshot["id"]).one_or_none()
            if competitor:
                competitor.last_error = str(exc)[:1000]
                competitor.last_synced_at = datetime.utcnow()
                db.session.commit()
            mark_credential_error(credential_id, exc, expired=is_auth_error(exc))
        finally:
            release_db_session_quietly()
        raise BilibiliIntelligenceError(f"B站投稿同步失败: {exc}") from exc

    rows = ((payload.get("list") or {}).get("vlist") or []) if isinstance(payload, dict) else []
    page = payload.get("page") if isinstance(payload, dict) and isinstance(payload.get("page"), dict) else {}
    values = []
    for item in rows:
        if isinstance(item, dict):
            try:
                values.append(_video_values(
                    item,
                    competitor_id=competitor_snapshot["id"],
                    content_type=competitor_snapshot["content_type"],
                ))
            except BilibiliIntelligenceError:
                LOGGER.warning("Skipped malformed Bilibili video for competitor %s", competitor_id)

    bvids = [item["bvid"] for item in values]
    existing = {
        item.bvid: item
        for item in BilibiliCompetitorVideo.query.options(load_only(
            BilibiliCompetitorVideo.id,
            BilibiliCompetitorVideo.bvid,
        )).filter(BilibiliCompetitorVideo.bvid.in_(bvids)).all()
    } if bvids else {}
    created = 0
    updated = 0
    for item in values:
        video = existing.get(item["bvid"])
        if video is None:
            video = BilibiliCompetitorVideo(**item)
            db.session.add(video)
            created += 1
        else:
            for key, value in item.items():
                setattr(video, key, value)
            updated += 1

    competitor = BilibiliCompetitor.query.filter(BilibiliCompetitor.id == competitor_snapshot["id"]).one()
    competitor.last_synced_at = datetime.utcnow()
    competitor.last_error = None
    competitor.video_count = _safe_int(page.get("count"))
    published_values = [item.get("published_at") for item in values if item.get("published_at")]
    if published_values:
        competitor.latest_published_at = max(published_values)
    db.session.commit()
    video_count = competitor.video_count
    latest_published_at = competitor.latest_published_at
    mark_credential_success(credential_id)
    result = {
        "competitor_id": competitor_snapshot["id"],
        "competitor_name": competitor_snapshot["name"],
        "fetched": len(values),
        "created": created,
        "updated": updated,
        "video_count": video_count,
        "latest_published_at": latest_published_at.isoformat() + "Z" if latest_published_at else None,
    }
    release_db_session_quietly()
    return result


def sync_all_competitors(*, page_size: int = 30) -> Dict[str, Any]:
    ids = [
        row.id
        for row in BilibiliCompetitor.query.with_entities(BilibiliCompetitor.id)
        .filter(BilibiliCompetitor.active.is_(True))
        .order_by(BilibiliCompetitor.content_type.asc(), BilibiliCompetitor.name.asc())
        .all()
    ]
    release_db_session_quietly()
    results = []
    errors = []
    for competitor_id in ids:
        try:
            results.append(sync_competitor(competitor_id, page_size=page_size))
        except Exception as exc:
            errors.append({"competitor_id": competitor_id, "error": str(exc)[:1000]})
    return {"synced": len(results), "failed": len(errors), "results": results, "errors": errors}


def normalize_match_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"\.(?:pdf|mp3|m4a|wav|aac|flac)$", "", text, flags=re.I)
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            text = text.replace(alias.lower(), canonical)
    text = _BOILERPLATE_RE.sub("", text)
    text = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return text


def _char_ngrams(text: str, size: int = 2) -> set:
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def _date_tokens(value: Any) -> set:
    output = set()
    for match in _DATE_RE.finditer(str(value or "")):
        year, month, day = match.groups()
        output.add(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    return output


def title_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_match_text(left)
    right_norm = normalize_match_text(right)
    if not left_norm or not right_norm:
        return 0.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_grams = _char_ngrams(left_norm)
    right_grams = _char_ngrams(right_norm)
    union = left_grams | right_grams
    jaccard = len(left_grams & right_grams) / len(union) if union else 0.0
    containment = (
        len(left_grams & right_grams) / min(len(left_grams), len(right_grams))
        if left_grams and right_grams else 0.0
    )
    score = sequence * 0.45 + jaccard * 0.3 + containment * 0.25
    left_dates = _date_tokens(left)
    right_dates = _date_tokens(right)
    if left_dates and right_dates:
        score += 0.08 if left_dates & right_dates else -0.08
    return round(max(0.0, min(1.0, score)), 4)


def _candidate_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        str(candidate.get(key) or "").strip()
        for key in ("title", "filename", "topic_title", "task_title", "institution", "theme")
    ).strip()


def _attachment_candidates(content_type: str, limit: int) -> List[Dict[str, Any]]:
    extensions = {"pdf"} if content_type == "report" else _AUDIO_EXTENSIONS
    recent = (
        db.session.query(
            ZsxqAttachment.id.label("id"),
            ZsxqAttachment.name.label("filename"),
            ZsxqAttachment.topic_id.label("topic_id"),
            ZsxqAttachment.tab_id.label("tab_id"),
            ZsxqAttachment.group_id.label("group_id"),
            ZsxqAttachment.updated_at.label("updated_at"),
            ZsxqAttachment.created_at.label("created_at"),
        )
        .filter(
            ZsxqAttachment.download_status == "downloaded",
            ZsxqAttachment.local_path.isnot(None),
            ZsxqAttachment.file_ext.in_(sorted(extensions)),
        )
        .order_by(ZsxqAttachment.updated_at.desc(), ZsxqAttachment.created_at.desc())
        .limit(limit)
        .subquery()
    )
    rows = (
        db.session.query(
            recent.c.id,
            recent.c.filename,
            recent.c.topic_id,
            recent.c.updated_at,
            recent.c.created_at,
            db.func.coalesce(ZsxqTopic.title, ZsxqTopic.article_title).label("topic_title"),
            ZsxqTopic.create_time.label("topic_created_at"),
        )
        .outerjoin(
            ZsxqTopic,
            and_(
                ZsxqTopic.group_id == recent.c.group_id,
                ZsxqTopic.tab_id == recent.c.tab_id,
                ZsxqTopic.topic_id == recent.c.topic_id,
            ),
        )
        .order_by(recent.c.updated_at.desc(), recent.c.created_at.desc())
        .all()
    )
    return [
        {
            "key": f"attachment:{row.id}",
            "local_type": "zsxq_attachment",
            "local_id": row.id,
            "title": row.topic_title or row.filename or "本地附件",
            "filename": row.filename or "",
            "topic_title": row.topic_title or "",
            "topic_id": row.topic_id,
            "source_date": row.topic_created_at or (
                row.updated_at.isoformat() if row.updated_at else (row.created_at.isoformat() if row.created_at else None)
            ),
        }
        for row in rows
    ]


def _workflow_candidates(content_type: str, limit: int) -> List[Dict[str, Any]]:
    features = _REPORT_FEATURES if content_type == "report" else _AUDIO_FEATURES
    if content_type == "report":
        query = PublicationTask.query.with_entities(
            PublicationTask.id,
            PublicationTask.source_task_id,
            PublicationTask.task_title,
            PublicationTask.status,
            PublicationTask.created_at,
            PublicationTask.report_audio_pdf_attachment_id,
            PublicationTask.report_audio_pdf_filename,
            PublicationTask.report_audio_pdf_topic_title,
            db.func.coalesce(
                PublicationTask.report_audio_structured_title,
                PublicationTask.report_audio_video_title,
                PublicationTask.report_audio_metadata_title,
            ).label("derived_title"),
        )
    else:
        query = PublicationTask.query.with_entities(
            PublicationTask.id,
            PublicationTask.source_task_id,
            PublicationTask.task_title,
            PublicationTask.status,
            PublicationTask.created_at,
            PublicationTask.mp3_audio_filename,
            PublicationTask.mp3_audio_topic_title,
        )
    rows = (
        query.filter(PublicationTask.feature.in_(sorted(features)))
        .order_by(PublicationTask.created_at.desc())
        .limit(limit)
        .all()
    )
    candidates = []
    for row in rows:
        if content_type == "report":
            filename = row.report_audio_pdf_filename or ""
            topic_title = row.report_audio_pdf_topic_title or ""
            title = (
                row.derived_title
                or row.task_title
                or topic_title
                or filename
                or "研报编排"
            )
            source_id = row.report_audio_pdf_attachment_id or row.source_task_id
        else:
            filename = row.mp3_audio_filename or ""
            topic_title = row.mp3_audio_topic_title or ""
            title = row.task_title or topic_title or filename or "MP3 编排"
            source_id = row.source_task_id
        candidates.append({
            "key": f"task:{row.id}",
            "local_type": "publication_task",
            "local_id": row.id,
            "title": title,
            "filename": filename,
            "topic_title": topic_title,
            "task_title": row.task_title or "",
            "source_attachment_id": source_id,
            "orchestration_status": row.status,
            "orchestration_task_id": row.id,
            "source_date": row.created_at.isoformat() if row.created_at else None,
        })
    return candidates


def _report_video_candidates(limit: int) -> List[Dict[str, Any]]:
    recent = (
        db.session.query(
            ReportVideoReport.id.label("id"),
            ReportVideoReport.task_id.label("task_id"),
            ReportVideoReport.file_name.label("file_name"),
            ReportVideoReport.created_at.label("created_at"),
        )
        .order_by(ReportVideoReport.created_at.desc())
        .limit(limit)
        .subquery()
    )
    rows = (
        db.session.query(
            recent.c.id,
            recent.c.task_id,
            recent.c.file_name,
            recent.c.created_at,
            ReportVideoAnalysis.report_title.label("report_title"),
            ReportVideoAnalysis.institution.label("institution"),
            ReportVideoAnalysis.theme.label("theme"),
            ReportVideoTask.status.label("task_status"),
        )
        .outerjoin(ReportVideoAnalysis, ReportVideoAnalysis.report_id == recent.c.id)
        .outerjoin(ReportVideoTask, ReportVideoTask.id == recent.c.task_id)
        .order_by(recent.c.created_at.desc())
        .all()
    )
    return [{
        "key": f"report:{row.id}",
        "local_type": "report_video_report",
        "local_id": row.id,
        "title": row.report_title or row.file_name or "研报视频素材",
        "filename": row.file_name or "",
        "topic_title": row.report_title or "",
        "institution": row.institution or "",
        "theme": row.theme or "",
        "orchestration_status": row.task_status,
        "orchestration_task_id": row.task_id,
        "source_date": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]


def load_local_candidates(content_type: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if content_type not in CONTENT_TYPES:
        raise BilibiliIntelligenceError("不支持的内容类型")
    configured_limit = _int_env(
        "BILIBILI_INTELLIGENCE_LOCAL_CANDIDATE_LIMIT",
        DEFAULT_LOCAL_CANDIDATE_LIMIT,
        lower=50,
        upper=MAX_LOCAL_CANDIDATE_LIMIT,
    )
    limit = max(50, min(MAX_LOCAL_CANDIDATE_LIMIT, int(limit or configured_limit)))
    secondary_limit = _int_env(
        "BILIBILI_INTELLIGENCE_SECONDARY_CANDIDATE_LIMIT",
        DEFAULT_SECONDARY_CANDIDATE_LIMIT,
        lower=20,
        upper=MAX_SECONDARY_CANDIDATE_LIMIT,
    )
    cache_key = f"{content_type}:{limit}:{secondary_limit}"
    cache_seconds = _int_env("BILIBILI_INTELLIGENCE_CANDIDATE_CACHE_SECONDS", 60, lower=1, upper=600)
    with _CANDIDATE_CACHE_LOCK:
        cached = _CANDIDATE_CACHE.get(cache_key)
        if cached and float(cached.get("expires_at") or 0) > time.time():
            return list(cached.get("items") or [])
    candidates = _attachment_candidates(content_type, limit)
    candidates.extend(_workflow_candidates(content_type, secondary_limit))
    if content_type == "report":
        candidates.extend(_report_video_candidates(secondary_limit))
    with _CANDIDATE_CACHE_LOCK:
        _CANDIDATE_CACHE[cache_key] = {
            "expires_at": time.time() + cache_seconds,
            "items": list(candidates),
        }
    return candidates


def rank_local_candidates(
    title: str,
    content_type: str,
    *,
    candidates: Optional[Sequence[Dict[str, Any]]] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    inventory = list(candidates) if candidates is not None else load_local_candidates(content_type)
    ranked = []
    for candidate in inventory:
        score = title_similarity(title, _candidate_text(candidate))
        ranked.append({**candidate, "rule_score": score})
    ranked.sort(key=lambda item: (item["rule_score"], item.get("source_date") or ""), reverse=True)
    return ranked[:max(1, min(20, int(limit or 8)))]


def _json_from_text(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("AI 返回的 JSON 根节点不是对象")
    return payload


def _deepseek_model() -> str:
    return (
        os.environ.get("BILIBILI_INTELLIGENCE_MATCH_MODEL")
        or os.environ.get("DEEPSEEK_FLASH_MODEL")
        or os.environ.get("DEEPSEEK_MODEL")
        or "deepseek-v4-flash"
    )


def deepseek_match_batch(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    api_key = os.environ.get("BILIBILI_INTELLIGENCE_MATCH_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise BilibiliIntelligenceError("缺少 DEEPSEEK_API_KEY，无法执行 AI 语义匹配")
    base_url = os.environ.get("BILIBILI_INTELLIGENCE_MATCH_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    timeout = _int_env("BILIBILI_INTELLIGENCE_MATCH_TIMEOUT", 120, lower=10, upper=300)
    system_prompt = (
        "你是财经内容来源核验助手。任务是判断 B站竞争对手投稿与本地 PDF/音频候选之间的关系。"
        "必须保守判断，不能因为行业相同就判为同一内容。same_source 表示同一份报告或同一场音频；"
        "same_event 表示同一场会议/同一报告事件但版本可能不同；same_topic 只表示主题相关；unrelated 表示无关。"
        "证据不足必须返回 insufficient_evidence。只允许选择输入中提供的 candidate key，严格输出 JSON。"
    )
    user_prompt = "\n".join([
        "请逐条判断，输出：",
        '{"matches":[{"video_id":"...","match_status":"matched|possible|related|not_found|insufficient_evidence",'
        '"candidate_key":"attachment:...或null","confidence":0到1,"relation":"same_source|same_event|same_topic|unrelated|unknown",'
        '"reason":"简短中文理由","evidence":["..."],"conflicts":["..."]}]}',
        "matched 只能用于 same_source/same_event 且证据充分；possible 用于可能对应但仍需人工确认；related 只代表同主题。",
        "不要强行匹配。日期冲突、机构冲突、公司或会议主题冲突必须写入 conflicts。",
        "",
        json.dumps({"items": items}, ensure_ascii=False),
    ])
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": _deepseek_model(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.05,
        },
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise BilibiliIntelligenceError(
            f"DS v4 flash 匹配失败: HTTP {response.status_code} {response.text[:600]}"
        ) from exc
    content = (((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise BilibiliIntelligenceError("DS v4 flash 未返回匹配结果")
    payload = _json_from_text(content)
    payload["provider"] = "deepseek"
    payload["model"] = _deepseek_model()
    return payload


def _clip_candidate_for_ai(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": candidate.get("key"),
        "local_type": candidate.get("local_type"),
        "title": str(candidate.get("title") or "")[:500],
        "filename": str(candidate.get("filename") or "")[:500],
        "topic_title": str(candidate.get("topic_title") or "")[:500],
        "institution": str(candidate.get("institution") or "")[:120],
        "theme": str(candidate.get("theme") or "")[:120],
        "source_date": candidate.get("source_date"),
        "rule_score": candidate.get("rule_score"),
    }


def _coerce_confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def _normalize_ai_match(raw: Dict[str, Any], allowed: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    status = str(raw.get("match_status") or "insufficient_evidence").strip().lower()
    if status not in {"matched", "possible", "related", "not_found", "insufficient_evidence"}:
        status = "insufficient_evidence"
    relation = str(raw.get("relation") or "unknown").strip().lower()
    if relation not in {"same_source", "same_event", "same_topic", "unrelated", "unknown"}:
        relation = "unknown"
    candidate_key = str(raw.get("candidate_key") or "").strip()
    candidate = allowed.get(candidate_key)
    confidence = _coerce_confidence(raw.get("confidence"))
    if candidate is None:
        candidate_key = ""
        if status in {"matched", "possible", "related"}:
            status = "insufficient_evidence"
            relation = "unknown"
            confidence = 0.0
    if status in {"not_found", "insufficient_evidence"} or relation == "unrelated":
        candidate = None
        candidate_key = ""
        if relation == "unrelated":
            status = "not_found"
    if status == "matched" and (relation not in {"same_source", "same_event"} or confidence < 0.75):
        status = "possible" if candidate else "insufficient_evidence"
    return {
        "match_status": status,
        "relation": relation,
        "candidate": candidate,
        "candidate_key": candidate_key or None,
        "confidence": confidence,
        "reason": str(raw.get("reason") or "").strip()[:2000],
        "evidence": [str(item)[:500] for item in (raw.get("evidence") or []) if str(item).strip()][:10],
        "conflicts": [str(item)[:500] for item in (raw.get("conflicts") or []) if str(item).strip()][:10],
    }


def _task_status_for_candidate(candidate: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not candidate:
        return {"status": None, "task_id": None}
    if candidate.get("orchestration_task_id"):
        return {
            "status": candidate.get("orchestration_status"),
            "task_id": candidate.get("orchestration_task_id"),
        }
    if candidate.get("local_type") != "zsxq_attachment":
        return {"status": None, "task_id": None}
    attachment_id = str(candidate.get("local_id") or "")
    row = (
        PublicationTask.query.with_entities(PublicationTask.id, PublicationTask.status, PublicationTask.updated_at)
        .filter(or_(
            PublicationTask.source_task_id == attachment_id,
            PublicationTask.report_audio_pdf_attachment_id == attachment_id,
        ))
        .order_by(PublicationTask.updated_at.desc())
        .first()
    )
    return {"status": row.status, "task_id": row.id} if row else {"status": "not_orchestrated", "task_id": None}


def match_videos(video_ids: Sequence[str]) -> Dict[str, Any]:
    normalized_ids = list(dict.fromkeys(str(item).strip() for item in video_ids if str(item).strip()))[:MATCH_BATCH_LIMIT]
    if not normalized_ids:
        raise BilibiliIntelligenceError("请选择需要匹配的投稿")
    rows = (
        BilibiliCompetitorVideo.query.with_entities(
            BilibiliCompetitorVideo.id,
            BilibiliCompetitorVideo.content_type,
            BilibiliCompetitorVideo.title,
            BilibiliCompetitorVideo.description,
            BilibiliCompetitorVideo.published_at,
        )
        .filter(BilibiliCompetitorVideo.id.in_(normalized_ids))
        .all()
    )
    if not rows:
        raise BilibiliIntelligenceError("没有找到需要匹配的投稿")

    inventories: Dict[str, List[Dict[str, Any]]] = {}
    request_items = []
    candidate_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        if row.content_type not in inventories:
            inventories[row.content_type] = load_local_candidates(row.content_type)
        candidates = rank_local_candidates(
            row.title,
            row.content_type,
            candidates=inventories[row.content_type],
            limit=8,
        )
        candidate_maps[row.id] = {str(item["key"]): item for item in candidates}
        request_items.append({
            "video_id": row.id,
            "content_type": row.content_type,
            "title": row.title,
            "description": str(row.description or "")[:800],
            "published_at": row.published_at.isoformat() + "Z" if row.published_at else None,
            "candidates": [_clip_candidate_for_ai(item) for item in candidates],
        })

    release_db_session_quietly()
    try:
        ai_payload = deepseek_match_batch(request_items)
    except Exception as exc:
        BilibiliCompetitorVideo.query.filter(BilibiliCompetitorVideo.id.in_(normalized_ids)).update(
            {
                BilibiliCompetitorVideo.match_status: "failed",
                BilibiliCompetitorVideo.match_method: "deepseek",
                BilibiliCompetitorVideo.match_error: str(exc)[:2000],
                BilibiliCompetitorVideo.matched_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.session.commit()
        release_db_session_quietly()
        raise

    raw_matches = ai_payload.get("matches") if isinstance(ai_payload.get("matches"), list) else []
    raw_by_id = {
        str(item.get("video_id")): item
        for item in raw_matches
        if isinstance(item, dict) and str(item.get("video_id") or "").strip()
    }
    results = []
    for row in rows:
        raw = raw_by_id.get(row.id) or {"match_status": "insufficient_evidence", "reason": "模型未返回该投稿的结果"}
        normalized = _normalize_ai_match(raw, candidate_maps.get(row.id, {}))
        candidate = normalized["candidate"]
        task_state = _task_status_for_candidate(candidate)
        local_exists = (
            True if normalized["match_status"] == "matched" and normalized["relation"] in {"same_source", "same_event"}
            else False if normalized["match_status"] in {"related", "not_found"}
            else None
        )
        match_payload = {
            "provider": ai_payload.get("provider"),
            "model": ai_payload.get("model"),
            "candidate_key": normalized["candidate_key"],
            "evidence": normalized["evidence"],
            "conflicts": normalized["conflicts"],
            "candidates": [_clip_candidate_for_ai(item) for item in candidate_maps.get(row.id, {}).values()],
        }
        matched_at = datetime.utcnow()
        BilibiliCompetitorVideo.query.filter(BilibiliCompetitorVideo.id == row.id).update(
            {
                BilibiliCompetitorVideo.match_status: normalized["match_status"],
                BilibiliCompetitorVideo.match_method: "deepseek",
                BilibiliCompetitorVideo.match_confidence: normalized["confidence"],
                BilibiliCompetitorVideo.match_relation: normalized["relation"],
                BilibiliCompetitorVideo.matched_local_type: candidate.get("local_type") if candidate else None,
                BilibiliCompetitorVideo.matched_local_id: candidate.get("local_id") if candidate else None,
                BilibiliCompetitorVideo.matched_local_title: candidate.get("title") if candidate else None,
                BilibiliCompetitorVideo.local_exists: local_exists,
                BilibiliCompetitorVideo.orchestration_status: task_state["status"],
                BilibiliCompetitorVideo.orchestration_task_id: task_state["task_id"],
                BilibiliCompetitorVideo.match_reason: normalized["reason"],
                BilibiliCompetitorVideo.match_error: None,
                BilibiliCompetitorVideo.match_payload: match_payload,
                BilibiliCompetitorVideo.matched_at: matched_at,
                BilibiliCompetitorVideo.updated_at: matched_at,
            },
            synchronize_session=False,
        )
        results.append({
            "video_id": row.id,
            "match_status": normalized["match_status"],
            "confidence": normalized["confidence"],
            "matched_local_title": candidate.get("title") if candidate else None,
            "orchestration_status": task_state["status"],
        })
    db.session.commit()
    release_db_session_quietly()
    return {"matched": len(results), "model": ai_payload.get("model"), "results": results}


def update_decision(video_id: str, *, status: str, note: str = "") -> Dict[str, Any]:
    status = str(status or "").strip().lower()
    if status not in DECISION_STATUSES:
        raise BilibiliIntelligenceError("不支持的人工决策状态")
    video_id = str(video_id)
    exists = BilibiliCompetitorVideo.query.with_entities(BilibiliCompetitorVideo.id).filter(
        BilibiliCompetitorVideo.id == video_id
    ).scalar()
    if not exists:
        raise BilibiliIntelligenceError("投稿不存在")
    decision_note = str(note or "").strip()[:2000] or None
    decided_at = None if status == "pending" else datetime.utcnow()
    BilibiliCompetitorVideo.query.filter(BilibiliCompetitorVideo.id == video_id).update(
        {
            BilibiliCompetitorVideo.decision_status: status,
            BilibiliCompetitorVideo.decision_note: decision_note,
            BilibiliCompetitorVideo.decided_at: decided_at,
            BilibiliCompetitorVideo.updated_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.session.commit()
    payload = {
        "id": video_id,
        "decision_status": status,
        "decision_note": decision_note,
        "decided_at": decided_at.isoformat() + "Z" if decided_at else None,
    }
    release_db_session_quietly()
    return payload
