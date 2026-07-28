import sys
import unittest
from pathlib import Path

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.zsxq_sync import ZsxqTopic, ZsxqTopicComment, ZsxqTopicFavorite  # noqa: E402
from services.zsxq_sync_service import ZsxqSyncService  # noqa: E402
from services.zsxq_yiren_award_cleanup_service import (  # noqa: E402
    YIREN_AWARD_NOTIFICATION_USER_ID,
    YIREN_PERSONAL_USER_ID,
    cleanup_yiren_award_favorites,
    is_yiren_award_comment,
)


class ZsxqYirenAwardCleanupTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        for topic_id in ("winner", "comment-only", "other-owner", "negated", "future-winner"):
            db.session.add(ZsxqTopic(
                group_id="1824528822",
                tab_id="latest",
                topic_id=topic_id,
                title=topic_id,
                create_time="2026-07-17T10:00:00+08:00",
                raw_payload={},
            ))
        self._add_comment("winner", "亦仁", "感谢分享，已中标", "2026-07-17T10:01:00+08:00")
        self._add_comment("comment-only", "亦仁", "你是个人才", "2026-07-17T10:02:00+08:00")
        self._add_comment("other-owner", "别人", "感谢分享，已中标", "2026-07-17T10:03:00+08:00")
        self._add_comment("negated", "亦仁", "这次未中标，继续加油", "2026-07-17T10:04:00+08:00")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _add_comment(self, topic_id, owner_name, text, create_time):
        db.session.add(ZsxqTopicComment(
            group_id="1824528822",
            tab_id="latest",
            topic_id=topic_id,
            comment_id=f"comment-{topic_id}",
            owner_name=owner_name,
            text=text,
            create_time=create_time,
            raw_payload={},
        ))

    def test_matcher_requires_explicit_non_negated_award_by_yiren(self):
        self.assertTrue(is_yiren_award_comment("亦仁", "感谢分享，已中标"))
        self.assertTrue(is_yiren_award_comment(" 亦仁 ", "恭喜\n入选"))
        self.assertFalse(is_yiren_award_comment("亦仁", "这次未中标"))
        self.assertFalse(is_yiren_award_comment("亦仁", "期待下一次分享"))
        self.assertFalse(is_yiren_award_comment("别人", "已中标"))

    def test_matcher_uses_stable_identity_and_positive_award_semantics(self):
        self.assertTrue(is_yiren_award_comment(
            "亦仁",
            "感谢分享，本期正式入选",
            owner_user_id=YIREN_AWARD_NOTIFICATION_USER_ID,
            group_id="1824528822",
        ))
        self.assertTrue(is_yiren_award_comment(
            "亦仁",
            "恭喜，这篇获选了",
            owner_user_id=YIREN_PERSONAL_USER_ID,
            group_id="1824528822",
        ))
        self.assertFalse(is_yiren_award_comment(
            "亦仁",
            "这篇有望中标",
            owner_user_id=YIREN_AWARD_NOTIFICATION_USER_ID,
            group_id="1824528822",
        ))
        self.assertFalse(is_yiren_award_comment(
            "亦仁",
            "感谢分享，已中标",
            owner_user_id="untrusted-same-name",
            group_id="1824528822",
        ))
        self.assertFalse(is_yiren_award_comment(
            "亦仁",
            "感谢分享，已中标",
            owner_user_id=YIREN_AWARD_NOTIFICATION_USER_ID,
            group_id="88885882121542",
        ))

    def test_cleanup_adds_only_missing_award_favorite_and_is_idempotent(self):
        first = cleanup_yiren_award_favorites()
        self.assertEqual(first["candidate_topics"], 1)
        self.assertEqual(first["favorites_added"], 1)
        self.assertEqual(
            [row.topic_id for row in ZsxqTopicFavorite.query.all()],
            ["winner"],
        )

        second = cleanup_yiren_award_favorites()
        self.assertEqual(second["already_favorited"], 1)
        self.assertEqual(second["favorites_added"], 0)
        self.assertEqual(ZsxqTopicFavorite.query.count(), 1)

    def test_sync_only_auto_favorites_explicit_award_reply(self):
        service = ZsxqSyncService()
        tab = {"id": "latest"}
        ordinary = {
            "topic_id": "comment-only",
            "show_comments": [{
                "comment_id": "sync-ordinary",
                "owner": {"name": "亦仁"},
                "text": "期待！",
            }],
        }
        winner = {
            "topic_id": "future-winner",
            "show_comments": [{
                "comment_id": "sync-winner",
                "owner": {
                    "name": "亦仁",
                    "user_id": YIREN_AWARD_NOTIFICATION_USER_ID,
                },
                "text": "感谢分享，本期入选",
            }],
        }

        self.assertFalse(service._upsert_topic_comments("1824528822", tab, ordinary)["favorite_added"])
        self.assertTrue(service._upsert_topic_comments("1824528822", tab, winner)["favorite_added"])
        db.session.commit()
        self.assertEqual(
            [row.topic_id for row in ZsxqTopicFavorite.query.all()],
            ["future-winner"],
        )


if __name__ == "__main__":
    unittest.main()
