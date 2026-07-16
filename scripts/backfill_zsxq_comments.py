#!/usr/bin/env python3
"""Backfill structured ZSXQ comments and favorite topics replied to by 亦仁."""

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from models import db  # noqa: E402
from models.zsxq_sync import ZsxqTopic, ZsxqTopicComment, ZsxqTopicFavorite  # noqa: E402
from services.platform_credential_service import resolve_zsxq_cookie  # noqa: E402
from services.zsxq_sync_service import zsxq_sync_service  # noqa: E402
from sqlalchemy.orm import load_only  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-truncated", action="store_true")
    parser.add_argument("--group-id")
    parser.add_argument("--batch-size", type=int, default=50)
    return parser.parse_args()


def main():
    args = parse_args()
    app = create_app()
    with app.app_context():
        cookie = None
        if args.fetch_truncated:
            cookie = resolve_zsxq_cookie(purpose="zsxq.sync", required=True)["cookie"]

        summary = {
            "topics_scanned": 0,
            "topics_with_comments": 0,
            "truncated_topics": 0,
            "truncated_topics_completed": 0,
            "comments_seen": 0,
            "comments_new": 0,
            "comments_updated": 0,
        }

        last_id = None
        batch_size = min(max(args.batch_size, 1), 100)
        while True:
            query = ZsxqTopic.query.options(load_only(
                ZsxqTopic.id,
                ZsxqTopic.group_id,
                ZsxqTopic.tab_id,
                ZsxqTopic.tab_label,
                ZsxqTopic.topic_id,
                ZsxqTopic.raw_json_path,
            ))
            if args.group_id:
                query = query.filter(ZsxqTopic.group_id == str(args.group_id))
            if last_id:
                query = query.filter(ZsxqTopic.id > last_id)
            topics = query.order_by(ZsxqTopic.id.asc()).limit(batch_size).all()
            if not topics:
                break

            for topic in topics:
                last_id = topic.id
                payload = zsxq_sync_service._topic_raw_payload_from_path(topic.raw_json_path)
                if not payload:
                    payload = (
                        db.session.query(ZsxqTopic.raw_payload)
                        .filter(ZsxqTopic.id == topic.id)
                        .scalar()
                    )
                payload = payload if isinstance(payload, dict) else {}
                if not payload:
                    summary["topics_scanned"] += 1
                    continue
                expected = int(payload.get("comments_count") or 0)
                shown = len(payload.get("show_comments") or [])
                was_truncated = expected > shown
                if was_truncated:
                    summary["truncated_topics"] += 1
                    if cookie:
                        zsxq_sync_service._hydrate_truncated_topic_comments(cookie, payload)
                        if not payload.get("comments_truncated"):
                            summary["truncated_topics_completed"] += 1
                        topic.raw_payload = payload
                        topic.raw_json_path = zsxq_sync_service._write_topic_json(
                            topic.group_id,
                            {"id": topic.tab_id, "label": topic.tab_label or topic.tab_id},
                            topic.topic_id,
                            payload,
                        )

                stats = zsxq_sync_service._upsert_topic_comments(
                    topic.group_id,
                    {"id": topic.tab_id, "label": topic.tab_label or topic.tab_id},
                    payload,
                )
                summary["topics_scanned"] += 1
                if stats["comments_seen"]:
                    summary["topics_with_comments"] += 1
                for key in ("comments_seen", "comments_new", "comments_updated"):
                    summary[key] += int(stats.get(key) or 0)
            db.session.commit()
            db.session.expunge_all()

        yiren_rows = (
            db.session.query(ZsxqTopicComment.group_id, ZsxqTopicComment.topic_id)
            .filter(ZsxqTopicComment.owner_name == "亦仁")
            .distinct()
            .all()
        )
        favorites_added = 0
        for group_id, topic_id in yiren_rows:
            exists = ZsxqTopicFavorite.query.filter_by(group_id=group_id, topic_id=topic_id).first()
            if exists is None:
                db.session.add(ZsxqTopicFavorite(group_id=group_id, topic_id=topic_id))
                favorites_added += 1
        db.session.commit()

        summary.update({
            "comments_in_table": ZsxqTopicComment.query.count(),
            "yiren_topics": len(yiren_rows),
            "yiren_winning_comments": ZsxqTopicComment.query.filter(
                ZsxqTopicComment.owner_name == "亦仁",
                ZsxqTopicComment.text.ilike("%已中标%"),
            ).count(),
            "favorites_added": favorites_added,
            "yiren_topics_favorited": sum(
                1
                for group_id, topic_id in yiren_rows
                if ZsxqTopicFavorite.query.filter_by(group_id=group_id, topic_id=topic_id).first() is not None
            ),
        })
        print(summary)


if __name__ == "__main__":
    main()
