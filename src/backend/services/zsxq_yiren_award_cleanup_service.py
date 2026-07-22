"""Detect Yiren award replies and backfill their local topic favorites."""

from datetime import datetime, timezone

from sqlalchemy.orm import load_only

from models import db
from models.zsxq_sync import ZsxqTopic, ZsxqTopicComment, ZsxqTopicFavorite


YIREN_NAME = "亦仁"
AWARD_MARKERS = ("中标", "入选")
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
)


def _compact_text(value):
    return "".join(str(value or "").split())


def is_yiren_award_comment(owner_name, text):
    """Return true only for an explicit, non-negated award reply by Yiren."""
    if _compact_text(owner_name) != YIREN_NAME:
        return False
    normalized = _compact_text(text)
    if not normalized or any(marker in normalized for marker in NEGATED_AWARD_MARKERS):
        return False
    return any(marker in normalized for marker in AWARD_MARKERS)


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


def cleanup_yiren_award_favorites(group_id="1824528822", since=None, dry_run=False):
    """Add missing local favorites for topics with explicit Yiren award replies."""
    group_id = str(group_id).strip()
    query = ZsxqTopicComment.query.options(
        load_only(
            ZsxqTopicComment.comment_id,
            ZsxqTopicComment.topic_id,
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
    matched = [comment for comment in comments if is_yiren_award_comment(comment.owner_name, comment.text)]
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
