from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_

from models import db
from models.bilibili_intelligence import BilibiliCompetitor, BilibiliCompetitorVideo
from models.bilibili_upower_archive import BilibiliUpowerArchiveStat, BilibiliUpowerTopicReport, BilibiliUpowerTopic
from services.bilibili_intelligence_service import (
    BilibiliIntelligenceError,
    CONTENT_TYPES,
    DECISION_STATUSES,
    match_videos,
    sync_all_competitors,
    sync_competitor,
    update_decision,
)
from services.bilibili_charging_dashboard_service import (
    BilibiliChargingDashboardError,
    charging_dashboard_payload,
    collect_charging_snapshots,
)
from services.runtime_guard import release_db_session_quietly
from utils.auth_utils import require_login


bp = Blueprint("bilibili_intelligence", __name__, url_prefix="/api/v1/bilibili-intelligence")


VIDEO_LIST_COLUMNS = (
    BilibiliCompetitorVideo.id,
    BilibiliCompetitorVideo.competitor_id,
    BilibiliCompetitor.name.label("competitor_name"),
    BilibiliCompetitorVideo.content_type,
    BilibiliCompetitorVideo.bvid,
    BilibiliCompetitorVideo.title,
    BilibiliCompetitorVideo.cover_url,
    BilibiliCompetitorVideo.duration_seconds,
    BilibiliCompetitorVideo.published_at,
    BilibiliCompetitorVideo.play_count,
    BilibiliCompetitorVideo.comment_count,
    BilibiliCompetitorVideo.danmaku_count,
    BilibiliCompetitorVideo.is_charging,
    BilibiliCompetitorVideo.match_status,
    BilibiliCompetitorVideo.match_method,
    BilibiliCompetitorVideo.match_confidence,
    BilibiliCompetitorVideo.match_relation,
    BilibiliCompetitorVideo.matched_local_type,
    BilibiliCompetitorVideo.matched_local_id,
    BilibiliCompetitorVideo.matched_local_title,
    BilibiliCompetitorVideo.local_exists,
    BilibiliCompetitorVideo.orchestration_status,
    BilibiliCompetitorVideo.orchestration_task_id,
    BilibiliCompetitorVideo.match_reason,
    BilibiliCompetitorVideo.match_error,
    BilibiliCompetitorVideo.matched_at,
    BilibiliCompetitorVideo.decision_status,
    BilibiliCompetitorVideo.decision_note,
    BilibiliCompetitorVideo.decided_at,
    BilibiliCompetitorVideo.first_seen_at,
    BilibiliCompetitorVideo.last_seen_at,
)


def _bounded_int(value, default, lower, upper):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _truthy(value, default=True):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _error(message, status=400):
    release_db_session_quietly()
    return jsonify({"success": False, "message": str(message)}), status


def _row_time(value):
    return value.isoformat() + "Z" if value else None


def _video_row_payload(row):
    return {
        "id": row.id,
        "competitor_id": row.competitor_id,
        "competitor_name": row.competitor_name,
        "content_type": row.content_type,
        "bvid": row.bvid,
        "video_url": f"https://www.bilibili.com/video/{row.bvid}/",
        "title": row.title,
        "cover_url": row.cover_url,
        "duration_seconds": row.duration_seconds,
        "published_at": _row_time(row.published_at),
        "play_count": row.play_count,
        "comment_count": row.comment_count,
        "danmaku_count": row.danmaku_count,
        "is_charging": bool(row.is_charging),
        "match_status": row.match_status,
        "match_method": row.match_method,
        "match_confidence": row.match_confidence,
        "match_relation": row.match_relation,
        "matched_local_type": row.matched_local_type,
        "matched_local_id": row.matched_local_id,
        "matched_local_title": row.matched_local_title,
        "local_exists": row.local_exists,
        "orchestration_status": row.orchestration_status,
        "orchestration_task_id": row.orchestration_task_id,
        "match_reason": row.match_reason,
        "match_error": row.match_error,
        "matched_at": _row_time(row.matched_at),
        "decision_status": row.decision_status,
        "decision_note": row.decision_note,
        "decided_at": _row_time(row.decided_at),
        "first_seen_at": _row_time(row.first_seen_at),
        "last_seen_at": _row_time(row.last_seen_at),
    }


@bp.get("/competitors")
@require_login
def list_competitors(_current_user):
    rows = BilibiliCompetitor.query.order_by(
        BilibiliCompetitor.content_type.asc(),
        BilibiliCompetitor.name.asc(),
    ).all()
    payload = [row.compact_dict() for row in rows]
    release_db_session_quietly()
    return jsonify({"success": True, "data": {"items": payload}})


@bp.get("/charging-dashboard")
@require_login
def charging_dashboard(_current_user):
    try:
        days = _bounded_int(request.args.get("days"), 31, 2, 366)
        return jsonify({"success": True, "data": charging_dashboard_payload(days=days)})
    except Exception as exc:
        return _error(f"读取 B站充电看板失败: {exc}", 500)


@bp.get("/upower-archive-stats")
@require_login
def upower_archive_stats(_current_user):
    """Read imported own-account UPower archive statistics."""
    try:
        page = _bounded_int(request.args.get("page"), 1, 1, 100000)
        page_size = _bounded_int(request.args.get("page_size"), 20, 1, 100)
        keyword = str(request.args.get("keyword") or "").strip()[:100]
        sort_by = str(request.args.get("sort_by") or "pub_time").strip()
        sort_dir = str(request.args.get("sort_dir") or "desc").strip().lower()
        sort_columns = {
            "pub_time": BilibiliUpowerArchiveStat.pub_time,
            "play_count": BilibiliUpowerArchiveStat.play_count,
            "income": BilibiliUpowerArchiveStat.income,
        }
        if sort_by not in sort_columns or sort_dir not in {"asc", "desc"}:
            return _error("排序参数无效")
        query = BilibiliUpowerArchiveStat.query.filter_by(period_days=90)
        overall_count, overall_income = db.session.query(
            func.count(BilibiliUpowerArchiveStat.id), func.coalesce(func.sum(BilibiliUpowerArchiveStat.income), 0)
        ).filter(BilibiliUpowerArchiveStat.period_days == 90).one()
        topic_id = request.args.get("topic_id", type=int)
        if request.args.get("topic_id") and not topic_id:
            return _error("话题参数无效")
        if topic_id:
            topic = db.session.get(BilibiliUpowerTopic, topic_id)
            if not topic:
                return _error("话题不存在", 404)
            report = db.session.get(BilibiliUpowerTopicReport, topic.report_id)
            query = query.filter(BilibiliUpowerArchiveStat.account_uid == report.account_uid,
                BilibiliUpowerArchiveStat.aid.in_(topic.archive_aids))
        if keyword:
            query = query.filter(BilibiliUpowerArchiveStat.title.ilike(f"%{keyword}%"))
        total = query.count()
        sort_column = sort_columns[sort_by]
        order = sort_column.asc().nullslast() if sort_dir == "asc" else sort_column.desc().nullslast()
        rows = query.order_by(order, BilibiliUpowerArchiveStat.aid.desc()).offset((page - 1) * page_size).limit(page_size).all()
        items = [{
            "aid": row.aid, "title": row.title, "cover_url": row.cover_url,
            "video_url": row.video_url, "pub_time": _row_time(row.pub_time),
            "duration_seconds": row.duration_seconds, "play_count": row.play_count,
            "income": row.income, "level_price": row.level_price,
            "level_name": row.level_name, "badge_text": row.badge_text,
        } for row in rows]
        latest = db.session.query(func.max(BilibiliUpowerArchiveStat.fetched_at)).filter_by(period_days=90).scalar()
        result = {"items": items, "total": total, "page": page, "page_size": page_size,
                  "sort_by": sort_by, "sort_dir": sort_dir,
                  "overall_count": overall_count, "overall_income_cents": int(overall_income),
                  "period_days": 90, "last_fetched_at": _row_time(latest)}
        release_db_session_quietly()
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return _error(f"读取 B站稿件充电数据失败: {exc}", 500)


@bp.get("/upower-topic-analysis")
@require_login
def upower_topic_analysis(_current_user):
    try:
        report = BilibiliUpowerTopicReport.query.filter_by(period_days=90).order_by(BilibiliUpowerTopicReport.created_at.desc()).first()
        if not report:
            return jsonify({"success": True, "data": None})
        topics = BilibiliUpowerTopic.query.filter_by(report_id=report.id).all()
        latest = db.session.query(func.max(BilibiliUpowerArchiveStat.fetched_at)).filter_by(account_uid=report.account_uid, period_days=90).scalar()
        payload = {"id": report.id, "created_at": _row_time(report.created_at),
            "source_fetched_at": _row_time(report.source_fetched_at), "summary": report.summary,
            "is_stale": bool(latest and latest > report.source_fetched_at),
            "topics": sorted([{"id": t.id, "name": t.name, "metrics": t.metrics, "conclusion": t.conclusion} for t in topics], key=lambda t:t["metrics"]["income_cents"], reverse=True)}
        release_db_session_quietly()
        return jsonify({"success": True, "data": payload})
    except Exception:
        return _error("读取话题分析失败", 500)


@bp.post("/charging-dashboard/sync")
@require_login
def sync_charging_dashboard(_current_user):
    try:
        result = collect_charging_snapshots(trigger="dashboard_manual")
        status = 200 if not result.get("failed") else 207
        return jsonify({"success": True, "data": result}), status
    except BilibiliChargingDashboardError as exc:
        return _error(exc, 502)
    except Exception as exc:
        return _error(f"同步 B站充电人数失败: {exc}", 502)


@bp.post("/competitors")
@require_login
def create_competitor(_current_user):
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid") or "").strip()
    name = str(data.get("name") or "").strip()
    content_type = str(data.get("content_type") or "").strip().lower()
    if not uid.isdigit():
        return _error("UID 必须是数字")
    if not name:
        return _error("请输入竞争对手名称")
    if content_type not in CONTENT_TYPES:
        return _error("内容类型只能是 report 或 audio")
    if BilibiliCompetitor.query.filter(BilibiliCompetitor.uid == uid).first():
        return _error("该 B站 UID 已经存在", 409)
    row = BilibiliCompetitor(
        name=name[:255],
        uid=uid[:32],
        content_type=content_type,
        source_url=str(data.get("source_url") or f"https://space.bilibili.com/{uid}/video").strip(),
        active=_truthy(data.get("active"), True),
    )
    db.session.add(row)
    db.session.commit()
    payload = row.compact_dict()
    release_db_session_quietly()
    return jsonify({"success": True, "data": payload}), 201


@bp.patch("/competitors/<competitor_id>")
@require_login
def update_competitor(_current_user, competitor_id):
    row = BilibiliCompetitor.query.filter(BilibiliCompetitor.id == str(competitor_id)).one_or_none()
    if not row:
        return _error("竞争对手不存在", 404)
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            return _error("竞争对手名称不能为空")
        row.name = name[:255]
    if "content_type" in data:
        content_type = str(data.get("content_type") or "").strip().lower()
        if content_type not in CONTENT_TYPES:
            return _error("内容类型只能是 report 或 audio")
        row.content_type = content_type
        BilibiliCompetitorVideo.query.filter(
            BilibiliCompetitorVideo.competitor_id == row.id
        ).update({BilibiliCompetitorVideo.content_type: content_type}, synchronize_session=False)
    if "source_url" in data:
        row.source_url = str(data.get("source_url") or "").strip() or None
    if "active" in data:
        row.active = _truthy(data.get("active"), row.active)
    db.session.commit()
    payload = row.compact_dict()
    release_db_session_quietly()
    return jsonify({"success": True, "data": payload})


@bp.post("/competitors/<competitor_id>/sync")
@require_login
def sync_one(_current_user, competitor_id):
    try:
        result = sync_competitor(
            competitor_id,
            page_size=_bounded_int((request.get_json(silent=True) or {}).get("page_size"), 30, 1, 50),
        )
        return jsonify({"success": True, "data": result})
    except BilibiliIntelligenceError as exc:
        return _error(exc, 502)
    except Exception as exc:
        return _error(f"同步失败: {exc}", 502)


@bp.post("/sync")
@require_login
def sync_all(_current_user):
    try:
        data = request.get_json(silent=True) or {}
        result = sync_all_competitors(page_size=_bounded_int(data.get("page_size"), 30, 1, 50))
        status = 200 if not result.get("failed") else 207
        return jsonify({"success": True, "data": result}), status
    except Exception as exc:
        return _error(f"同步失败: {exc}", 502)


@bp.get("/videos")
@require_login
def list_videos(_current_user):
    page = _bounded_int(request.args.get("page"), 1, 1, 100000)
    per_page = _bounded_int(request.args.get("per_page"), 30, 1, 100)
    content_type = str(request.args.get("content_type") or "").strip().lower()
    match_status = str(request.args.get("match_status") or "").strip().lower()
    decision_status = str(request.args.get("decision_status") or "").strip().lower()
    competitor_id = str(request.args.get("competitor_id") or "").strip()
    query_text = " ".join(str(request.args.get("q") or "").split())[:100]
    range_days = _bounded_int(request.args.get("range_days"), 30, 1, 3650)

    query = db.session.query(*VIDEO_LIST_COLUMNS).join(
        BilibiliCompetitor,
        BilibiliCompetitor.id == BilibiliCompetitorVideo.competitor_id,
    ).filter(BilibiliCompetitorVideo.published_at >= datetime.utcnow() - timedelta(days=range_days))
    if content_type in CONTENT_TYPES:
        query = query.filter(BilibiliCompetitorVideo.content_type == content_type)
    if match_status:
        query = query.filter(BilibiliCompetitorVideo.match_status == match_status)
    if decision_status in DECISION_STATUSES:
        query = query.filter(BilibiliCompetitorVideo.decision_status == decision_status)
    if competitor_id:
        query = query.filter(BilibiliCompetitorVideo.competitor_id == competitor_id)
    if query_text:
        pattern = f"%{query_text.replace('%', '').replace('_', '')}%"
        query = query.filter(or_(
            BilibiliCompetitorVideo.title.ilike(pattern),
            BilibiliCompetitorVideo.matched_local_title.ilike(pattern),
            BilibiliCompetitor.name.ilike(pattern),
        ))

    total = int(query.order_by(None).with_entities(func.count(BilibiliCompetitorVideo.id)).scalar() or 0)
    rows = (
        query.order_by(BilibiliCompetitorVideo.published_at.desc(), BilibiliCompetitorVideo.first_seen_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    items = [_video_row_payload(row) for row in rows]
    release_db_session_quietly()
    return jsonify({
        "success": True,
        "data": {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": ((total + per_page - 1) // per_page) if total else 0,
            },
        },
    })


@bp.get("/videos/<video_id>")
@require_login
def video_detail(_current_user, video_id):
    row = BilibiliCompetitorVideo.query.filter(BilibiliCompetitorVideo.id == str(video_id)).one_or_none()
    if not row:
        return _error("投稿不存在", 404)
    payload = row.detail_dict()
    release_db_session_quietly()
    return jsonify({"success": True, "data": payload})


@bp.post("/match")
@require_login
def run_match(_current_user):
    data = request.get_json(silent=True) or {}
    video_ids = data.get("video_ids") if isinstance(data.get("video_ids"), list) else []
    if not video_ids:
        limit = _bounded_int(data.get("limit"), 5, 1, 10)
        content_type = str(data.get("content_type") or "").strip().lower()
        query = BilibiliCompetitorVideo.query.with_entities(BilibiliCompetitorVideo.id).filter(
            BilibiliCompetitorVideo.match_status.in_(["pending", "failed"])
        )
        if content_type in CONTENT_TYPES:
            query = query.filter(BilibiliCompetitorVideo.content_type == content_type)
        video_ids = [row.id for row in query.order_by(BilibiliCompetitorVideo.published_at.desc()).limit(limit).all()]
    try:
        result = match_videos(video_ids)
        return jsonify({"success": True, "data": result})
    except BilibiliIntelligenceError as exc:
        return _error(exc, 502)
    except Exception as exc:
        return _error(f"AI 匹配失败: {exc}", 502)


@bp.patch("/videos/<video_id>/decision")
@require_login
def decide(_current_user, video_id):
    data = request.get_json(silent=True) or {}
    try:
        payload = update_decision(
            video_id,
            status=data.get("status"),
            note=data.get("note") or "",
        )
        return jsonify({"success": True, "data": payload})
    except BilibiliIntelligenceError as exc:
        return _error(exc, 400)


@bp.get("/overview")
@require_login
def overview(_current_user):
    range_days = _bounded_int(request.args.get("range_days"), 30, 1, 3650)
    since = datetime.utcnow() - timedelta(days=range_days)
    total = int(BilibiliCompetitorVideo.query.filter(BilibiliCompetitorVideo.published_at >= since).count())
    match_rows = (
        db.session.query(BilibiliCompetitorVideo.match_status, func.count(BilibiliCompetitorVideo.id))
        .filter(BilibiliCompetitorVideo.published_at >= since)
        .group_by(BilibiliCompetitorVideo.match_status)
        .all()
    )
    type_rows = (
        db.session.query(BilibiliCompetitorVideo.content_type, func.count(BilibiliCompetitorVideo.id))
        .filter(BilibiliCompetitorVideo.published_at >= since)
        .group_by(BilibiliCompetitorVideo.content_type)
        .all()
    )
    decision_rows = (
        db.session.query(BilibiliCompetitorVideo.decision_status, func.count(BilibiliCompetitorVideo.id))
        .filter(BilibiliCompetitorVideo.published_at >= since)
        .group_by(BilibiliCompetitorVideo.decision_status)
        .all()
    )
    latest_sync = db.session.query(func.max(BilibiliCompetitor.last_synced_at)).scalar()
    payload = {
        "range_days": range_days,
        "total": total,
        "by_match_status": {key or "unknown": int(value) for key, value in match_rows},
        "by_content_type": {key or "unknown": int(value) for key, value in type_rows},
        "by_decision_status": {key or "unknown": int(value) for key, value in decision_rows},
        "latest_sync_at": _row_time(latest_sync),
    }
    release_db_session_quietly()
    return jsonify({"success": True, "data": payload})
