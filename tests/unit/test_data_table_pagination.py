import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask
from sqlalchemy import event


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import Channel, ChannelData, User, db  # noqa: E402
from services.data_service import DataService  # noqa: E402


class DataTablePaginationTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        user = User(
            username="table-owner",
            email="table-owner@example.com",
            password_hash="fixture",
        )
        db.session.add(user)
        db.session.flush()
        self.user_id = user.id

        channels = []
        for index in range(3):
            channel = Channel(
                user_id=user.id,
                name=f"channel-{index}",
                channel_id=f"external-{index}",
                url=f"https://example.com/{index}",
                cpm=1,
                created_at=datetime(2026, 8, 1, 0, index),
            )
            db.session.add(channel)
            channels.append(channel)
        db.session.flush()

        first_day = date(2026, 8, 1)
        for day_offset in range(5):
            day = first_day + timedelta(days=day_offset)
            for channel in channels:
                db.session.add(
                    ChannelData(
                        channel_id=channel.id,
                        date=day,
                        subscribers=day_offset * 100 + channel.id,
                        total_views=day_offset * 1000 + channel.id,
                        video_count=day_offset,
                    )
                )
        db.session.commit()
        db.session.expunge_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_table_page_is_selected_in_sql_before_channel_data_hydration(self):
        statements = []
        loaded_channel_data = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                statements.append(statement)

        def capture_load(target, _context):
            loaded_channel_data.append((target.channel_id, target.date))

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        event.listen(ChannelData, "load", capture_load)
        try:
            result = DataService.get_table_data(
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 5),
                active_only=True,
                user_id=self.user_id,
                page=2,
                per_page=2,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)
            event.remove(ChannelData, "load", capture_load)

        self.assertTrue(result["success"], result)
        payload = result["data"]
        self.assertEqual(payload["total_days"], 5)
        self.assertEqual(
            [row["date"] for row in payload["rows"]],
            ["2026-08-03", "2026-08-02"],
        )
        self.assertEqual(len(payload["channels"]), 3)
        self.assertTrue(all(len(row["channels_data"]) == 3 for row in payload["rows"]))

        self.assertEqual(len(loaded_channel_data), 6)
        self.assertEqual(len(statements), 2, statements)
        self.assertIn("LIMIT", statements[-1].upper())
        self.assertIn("OFFSET", statements[-1].upper())

    def test_unpaged_export_contract_still_returns_the_full_range(self):
        result = DataService.get_table_data(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
            active_only=True,
            user_id=self.user_id,
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["total_days"], 5)
        self.assertEqual(len(result["data"]["rows"]), 5)


if __name__ == "__main__":
    unittest.main()
