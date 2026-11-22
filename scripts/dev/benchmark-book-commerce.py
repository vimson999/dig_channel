#!/usr/bin/env python3
"""Benchmark synthetic data only; refuses databases outside localhost bc_test_*."""
import os
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts' / 'maintenance'))
from models import User, db
from models.book_commerce import Account, AccountListing, Book, Listing, Observation, Video, VideoListingLink
from services.book_commerce.analysis import calculate
from apis.v1.book_commerce import bp
from migrate_book_commerce import upgrade


def chunks(items, size=2000):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def main():
    url = os.environ.get('BOOK_COMMERCE_TEST_DATABASE_URL', '')
    parsed = make_url(url)
    if parsed.host not in ('localhost', '127.0.0.1') or not parsed.database or not parsed.database.startswith('bc_test_'):
        raise RuntimeError('benchmark requires disposable localhost bc_test_* database')
    app = Flask(__name__)
    app.config.update(TESTING=True, ENABLE_BOOK_COMMERCE=True, SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False, SECRET_KEY='test')
    db.init_app(app)
    app.register_blueprint(bp)
    with app.app_context():
        User.__table__.create(db.engine)
        upgrade(db.engine)
        db.session.add(User(id=1, username='benchmark', email='benchmark@example.com', password_hash='unused'))
        base = datetime(2026, 10, 1, 4)
        accounts = [dict(id=f'a{i}', source='bench', source_key=f'a{i}', name=f'账号{i}', platform='wechat_channels') for i in range(100)]
        listings = [dict(id=f'l{i}', source='bench', source_key=f'l{i}', title=f'商品{i}', platform='wechat_channels') for i in range(2000)]
        account_listings = [dict(id=f'al{i}', account_id=f'a{i//20}', listing_id=f'l{i}', first_seen_at=base, last_seen_at=base, status='active') for i in range(2000)]
        videos = [dict(id=f'v{i}', source='bench', source_key=f'v{i}', account_id=f'a{i//100}', platform='wechat_channels', title=f'视频{i}', published_at=base-timedelta(days=1)) for i in range(10000)]
        links = [dict(id=f'link{i}', video_id=f'v{i}', account_listing_id=f'al{i//5}', valid_from=base-timedelta(days=1), observed_at=base, relation_type='attached', verification_status='confirmed') for i in range(10000)]
        for model, rows in ((Account, accounts), (Listing, listings), (AccountListing, account_listings), (Video, videos), (VideoListingLink, links)):
            for part in chunks(rows):
                db.session.bulk_insert_mappings(model, part)
            db.session.flush()
        observations = []
        sequence = 0
        for kind, count, days in (('video', 10000, 8), ('showcase', 2000, 10)):
            for day in range(days):
                current = base + timedelta(days=day)
                for i in range(count):
                    sequence += 1
                    if kind == 'video':
                        target, account, fields = f'video:v{i}', f'a{i//100}', {'video_id':f'v{i}'}
                        values, quality, scope = {'shares': str(100 + day * (110 if i % 10 == 0 else 50))}, {'shares':'exact'}, None
                    else:
                        target, account, fields = f'account_listing:al{i}', f'a{i//20}', {'account_listing_id':f'al{i}', 'listing_id':f'l{i}'}
                        values, quality, scope = {'cumulative_sales':str(1000 + day * 10)}, {'cumulative_sales':'exact'}, 'account_listing'
                    observations.append(dict(id=f'o{sequence}', kind=kind, account_id=account, target_key=target, series_key=f'bench:{scope or kind}:v1',
                                             slot_key=f'{sequence:064x}', content_hash=f'{sequence:064x}', observed_at=current, business_date=current.date(),
                                             metric_scope=scope, values=values, quality=quality, **fields))
                    if len(observations) == 2000:
                        db.session.bulk_insert_mappings(Observation, observations)
                        observations.clear()
        if observations:
            db.session.bulk_insert_mappings(Observation, observations)
        db.session.commit()
        started = time.perf_counter()
        run = calculate(db.session, base.date(), (base + timedelta(days=9)).date(), actor_id=1)
        elapsed = time.perf_counter() - started
        client = app.test_client()
        durations = []
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
            for _ in range(22):
                t = time.perf_counter()
                response = client.get('/api/v1/book-commerce/rankings/videos?date=2026-10-08')
                durations.append(time.perf_counter() - t)
                if response.status_code != 200:
                    raise RuntimeError(f'ranking returned {response.status_code}')
        p95 = sorted(durations[2:])[18]
        print(f'observations={sequence} accounts=100 listings=2000 videos=10000 analysis_seconds={elapsed:.3f} hot_rank_p95_seconds={p95:.3f} run={run.status}')
        if elapsed > 60 or p95 > 1:
            raise RuntimeError('benchmark target exceeded')
    return 0


if __name__ == '__main__':
    sys.exit(main())
