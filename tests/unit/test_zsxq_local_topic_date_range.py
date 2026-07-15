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


class ZsxqLocalTopicDateRangeTest(unittest.TestCase):
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

        for index, create_time in enumerate(
            (
                "2026-07-12T23:59:59+08:00",
                "2026-07-13T00:00:00+08:00",
                "2026-07-14 12:00:00+08:00",
                "2026-07-15T23:59:59+08:00",
                "2026-07-16T00:00:00+08:00",
            )
        ):
            db.session.add(
                ZsxqTopic(
                    group_id="group-1",
                    tab_id="tab-1",
                    topic_id=f"topic-{index}",
                    title=f"主题 {index}",
                    create_time=create_time,
                    raw_payload={},
                )
            )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_range_includes_both_boundary_dates(self):
        result = ZsxqSyncService().list_cached_topics(
            group_id="group-1",
            tab_id="tab-1",
            publish_date_start="2026-07-13",
            publish_date_end="2026-07-15",
        )

        topics = result["resp_data"]["topics"]
        self.assertEqual(result["resp_data"]["total"], 3)
        self.assertEqual(
            [topic["create_time"] for topic in topics],
            [
                "2026-07-15T23:59:59+08:00",
                "2026-07-14 12:00:00+08:00",
                "2026-07-13T00:00:00+08:00",
            ],
        )

    def test_legacy_publish_date_still_filters_one_day(self):
        result = ZsxqSyncService().list_cached_topics(
            group_id="group-1",
            tab_id="tab-1",
            publish_date="2026-07-14",
        )

        self.assertEqual(result["resp_data"]["total"], 1)
        self.assertEqual(result["resp_data"]["topics"][0]["topic_id"], "topic-2")

    def test_reversed_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            ZsxqSyncService().list_cached_topics(
                group_id="group-1",
                publish_date_start="2026-07-15",
                publish_date_end="2026-07-13",
            )


if __name__ == "__main__":
    unittest.main()
