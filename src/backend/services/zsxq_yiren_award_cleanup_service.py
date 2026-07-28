"""Detect Yiren award replies and backfill their local topic favorites."""

from datetime import datetime, timezone
import re

from sqlalchemy.orm import load_only

from models import db
from models.zsxq_sync import ZsxqTopic, ZsxqTopicComment, ZsxqTopicFavorite


SHENGCAI_GROUP_ID = "1824528822"
YIREN_NAME = "亦仁"
YIREN_AWARD_NOTIFICATION_USER_ID = "844145551241182"
YIREN_PERSONAL_USER_ID = "552158581144"
TRUSTED_YIREN_USER_IDS = {
    YIREN_AWARD_NOTIFICATION_USER_ID,
    YIREN_PERSONAL_USER_ID,
}
AWARD_MARKERS = ("中标", "入选", "获选", "被选中", "选上", "收录")
NEGATED_AWARD_MARKERS = (
    "未中标",
    "没中标",
    "没有中标",
    "不中标",
    "未能中标",
    "未入选",
    "没入选",
    "没有入选",
    "不入选",
    "未能入选",
    "未获选",
    "没获选",
    "没有获选",
    "未被选中",
    "没被选中",
    "没有被选中",
    "还没中标",
    "尚未中标",
    "还没入选",
    "尚未入选",
)
UNCERTAIN_OR_DISCUSSION_MARKERS = (
    "可能中标",
    "有望中标",
    "希望中标",
    "争取中标",
    "期待中标",
    "如果中标",
    "是否中标",
    "能否中标",
    "怎么中标",
    "如何中标",
    "中标标准",
    "中标规则",
    "中标方法",
    "中标案例",
    "可能入选",
    "有望入选",
    "希望入选",
    "争取入选",
    "期待入选",
    "如果入选",
    "是否入选",
    "能否入选",
    "怎么入选",
    "如何入选",
    "入选标准",
    "入选规则",
    "入选名单",
    "候选",
    "待定",
)
EXPLICIT_AWARD_PATTERNS = (
    re.compile(r"(?:已|成功|正式|确认|确定|恭喜|祝贺).{0,10}(?:中标|入选|获选|被选中|选上|收录)"),
    re.compile(r"(?:感谢分享|这篇|此帖|该帖|这个帖子).{0,12}(?:中标|入选|获选|被选中|选上|收录)"),
    re.compile(r"(?:中标|入选|获选|被选中|选上|收录)(?:了|啦|成功|通知)$"),
)


def _compact_text(value):
    return "".join(str(value or "").split())


def is_yiren_award_comment(
    owner_name,
    text,
    owner_user_id=None,
    group_id=None,
):
    """Return true for a confident, non-negated Yiren award notification."""
    if group_id not in (None, "") and str(group_id).strip() != SHENGCAI_GROUP_ID:
        return False
    if _compact_text(owner_name) != YIREN_NAME:
        return False
    normalized_user_id = str(owner_user_id or "").strip()
    if normalized_user_id and normalized_user_id not in TRUSTED_YIREN_USER_IDS:
        return False
    normalized = _compact_text(text)
    if (
        not normalized
        or not any(marker in normalized for marker in AWARD_MARKERS)
        or any(marker in normalized for marker in NEGATED_AWARD_MARKERS)
        or any(marker in normalized for marker in UNCERTAIN_OR_DISCUSSION_MARKERS)
    ):
        return False
    if normalized_user_id == YIREN_AWARD_NOTIFICATION_USER_ID:
        return True
    if any(pattern.search(normalized) for pattern in EXPLICIT_AWARD_PATTERNS):
        return True
    # Very short standalone confirmations such as “中标了” are explicit,
    # while longer conversational uses require one of the patterns above.
    return len(normalized) <= 8


def _topic_titles(group_id, topic_ids):
    titles = {}
    topic_ids = list(topic_ids)
    for start in range(0, len(topic_ids), 500):
        rows = (
            ZsxqTopic.query.options(load_only(ZsxqTopic.topic_id, ZsxqTopic.title))
            .filter(
                ZsxqTopic.group_id == group_id,
                ZsxqTopic.topic_id.in_(topic_ids[start:start + 500]),
            )
            .all()
        )
        for row in rows:
            titles.setdefault(str(row.topic_id), row.title)
    return titles


def cleanup_yiren_award_favorites(group_id=SHENGCAI_GROUP_ID, since=None, dry_run=False):
    """Add missing local favorites for topics with explicit Yiren award replies."""
    group_id = str(group_id).strip()
    query = ZsxqTopicComment.query.options(
        load_only(
            ZsxqTopicComment.comment_id,
            ZsxqTopicComment.topic_id,
            ZsxqTopicComment.owner_user_id,
            ZsxqTopicComment.owner_name,
            ZsxqTopicComment.text,
            ZsxqTopicComment.create_time,
        )
    ).filter(
        ZsxqTopicComment.group_id == group_id,
        ZsxqTopicComment.owner_name == YIREN_NAME,
    )
    if since:
        query = query.filter(ZsxqTopicComment.create_time >= str(since))

    comments = query.order_by(ZsxqTopicComment.create_time.desc()).all()
    matched = [
        comment
        for comment in comments
        if is_yiren_award_comment(
            comment.owner_name,
            comment.text,
            owner_user_id=comment.owner_user_id,
            group_id=group_id,
        )
    ]
    evidence_by_topic = {}
    for comment in matched:
        evidence_by_topic.setdefault(str(comment.topic_id), {
            "comment_id": str(comment.comment_id),
            "comment_text": comment.text,
            "comment_time": comment.create_time,
        })

    topic_ids = list(evidence_by_topic)
    existing_ids = set()
    for start in range(0, len(topic_ids), 500):
        rows = (
            ZsxqTopicFavorite.query.options(load_only(ZsxqTopicFavorite.topic_id))
            .filter(
                ZsxqTopicFavorite.group_id == group_id,
                ZsxqTopicFavorite.topic_id.in_(topic_ids[start:start + 500]),
            )
            .all()
        )
        existing_ids.update(str(row.topic_id) for row in rows)

    missing_ids = [topic_id for topic_id in topic_ids if topic_id not in existing_ids]
    if not dry_run:
        for topic_id in missing_ids:
            db.session.add(ZsxqTopicFavorite(group_id=group_id, topic_id=topic_id))
        db.session.commit()

    titles = _topic_titles(group_id, topic_ids)
    samples = []
    for topic_id, evidence in list(evidence_by_topic.items())[:50]:
        samples.append({
            "topic_id": topic_id,
            "title": titles.get(topic_id),
            **evidence,
            "was_favorite": topic_id in existing_ids,
            "favorite_added": topic_id in missing_ids and not dry_run,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "group_id": group_id,
        "since": str(since) if since else None,
        "dry_run": bool(dry_run),
        "scanned_comments": len(comments),
        "matched_comments": len(matched),
        "candidate_topics": len(topic_ids),
        "already_favorited": len(existing_ids),
        "missing_favorites": len(missing_ids),
        "favorites_added": 0 if dry_run else len(missing_ids),
        "samples": samples,
    }
