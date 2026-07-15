import sys
import unittest
from pathlib import Path

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.zsxq_sync import ZsxqTopic  # noqa: E402
from services.zsxq_sync_service import ZsxqSyncService  # noqa: E402


class ZsxqTopicFavoritesTest(unittest.TestCase):
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
        for index in range(2):
            db.session.add(ZsxqTopic(
                group_id="1824528822",
                tab_id="latest",
                topic_id=f"topic-{index}",
                title=f"主题 {index}",
                create_time=f"2026-07-15T10:0{index}:00+08:00",
                raw_payload={},
            ))
        db.session.commit()
        self.service = ZsxqSyncService()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_favorite_is_persisted_returned_and_filterable(self):
        result = self.service.set_topic_favorite("1824528822", "topic-0", True)
        self.assertTrue(result["is_favorite"])
        self.assertEqual(result["favorite_total"], 1)

        all_topics = self.service.list_cached_topics(group_id="1824528822", tab_id="latest")
        by_id = {topic["topic_id"]: topic for topic in all_topics["resp_data"]["topics"]}
        self.assertTrue(by_id["topic-0"]["is_favorite"])
        self.assertFalse(by_id["topic-1"]["is_favorite"])
        self.assertEqual(all_topics["resp_data"]["favorite_total"], 1)

        favorites = self.service.list_cached_topics(
            group_id="1824528822",
            tab_id="latest",
            favorite_only=True,
        )
        self.assertEqual([topic["topic_id"] for topic in favorites["resp_data"]["topics"]], ["topic-0"])

    def test_unfavorite_removes_database_record(self):
        self.service.set_topic_favorite("1824528822", "topic-0", True)
        result = self.service.set_topic_favorite("1824528822", "topic-0", False)
        self.assertFalse(result["is_favorite"])
        self.assertEqual(result["favorite_total"], 0)


if __name__ == "__main__":
    unittest.main()
