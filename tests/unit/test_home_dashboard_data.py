import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from models import Channel, ChannelData, User, db
from services.data_service import DataService


class HomeDashboardDataTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://',
                               SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(username='home-test', email='home@example.test', password_hash='fixture')
        db.session.add(user)
        db.session.flush()
        self.user_id = user.id
        self.channel = Channel(user_id=user.id, name='Zero is valid', channel_id='external-1',
                               url='https://example.test/1', cpm=1)
        db.session.add(self.channel)
        db.session.flush()
        db.session.add(ChannelData(channel_id=self.channel.id, date=date.today() - timedelta(days=1),
                                   subscribers=0, total_views=0, video_count=0, estimated_revenue=0))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_daily_missing_dates_are_null_but_observed_zero_is_preserved(self):
        for metric in ('revenue', 'subscribers', 'views'):
            result = DataService.get_chart_data(chart_type=metric, time_range=3, user_id=self.user_id)
            self.assertTrue(result['success'], result)
            self.assertEqual(result['data']['chart_data']['series'][0]['data'], [None, 0, None])
            self.assertEqual(result['data']['latest_data_date'], (date.today() - timedelta(days=1)).isoformat())

    def test_months_without_snapshots_are_not_zero(self):
        result = DataService.get_chart_data(chart_type='revenue', group='monthly', months=3, user_id=self.user_id)
        self.assertTrue(result['success'], result)
        self.assertIsNone(result['data']['chart_data']['series'][0]['data'][0])

    def test_missing_table_snapshot_has_explicit_presence_flag(self):
        other = Channel(user_id=self.user_id, name='No snapshot', channel_id='external-2',
                        url='https://example.test/2', cpm=1)
        db.session.add(other)
        db.session.commit()
        result = DataService.get_table_data(user_id=self.user_id)
        row = result['data']['rows'][0]['channels_data']
        self.assertIs(row[0].get('has_data'), True)
        self.assertIs(row[1].get('has_data'), False)
