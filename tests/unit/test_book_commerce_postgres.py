"""PostgreSQL-specific integration tests; use only an isolated disposable database."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from flask import Flask
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import User, db
from models.book_commerce import Account, Observation, State
from services.book_commerce.ingest import ImportConflict, import_batch
from test_book_commerce_ingest import payload


@pytest.fixture
def pg_app(monkeypatch):
    from datetime import datetime, timezone
    from services.book_commerce import ingest
    monkeypatch.setattr(ingest, '_utcnow', lambda: datetime(2026, 11, 19, tzinfo=timezone.utc))
    url = os.environ.get('BOOK_COMMERCE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires isolated PostgreSQL')
    parsed = make_url(url)
    if not parsed.database or not parsed.database.startswith('bc_test_') or parsed.host not in ('localhost', '127.0.0.1'):
        raise RuntimeError('PostgreSQL test database must be a disposable localhost bc_test_* database')
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        tables = [t for t in db.metadata.sorted_tables if t.name == 'users' or t.name.startswith('bc_')]
        db.metadata.create_all(db.engine, tables=tables)
        user = User(username='test', email='test@example.com', password_hash='unused')
        db.session.add(user)
        db.session.add(State(id=1, input_revision=0, active_rules_version=1, rules_config={}))
        db.session.add(Account(id='a', name='账号', source='fixture', source_key='a', platform='wechat_channels'))
        db.session.commit()
        assert user.id == 1
    yield app
    with app.app_context():
        db.session.remove()
        db.metadata.drop_all(db.engine, tables=tables)
        db.engine.dispose()


def test_concurrent_conflicting_imports_do_not_overwrite(pg_app):
    barrier = Barrier(2)
    def submit(value, key):
        with pg_app.app_context():
            item = payload(value)
            barrier.wait(timeout=5)
            try:
                import_batch(db.session, item, actor_id=1, idempotency_key=key)
                return 'committed'
            except ImportConflict:
                return 'conflict'
            finally:
                db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit, 335, 'one'), pool.submit(submit, 336, 'two')]
        results = [f.result(timeout=15) for f in futures]
    assert sorted(results) == ['committed', 'conflict']
    with pg_app.app_context():
        assert db.session.query(Observation).count() == 1
        assert db.session.get(State, 1).input_revision == 1


def test_concurrent_import_invalidates_inflight_analysis_without_blocking(pg_app):
    from datetime import date
    from threading import Event
    from unittest.mock import patch
    from models.book_commerce import DailyMetric
    from services.book_commerce import analysis
    from test_book_commerce_analysis import seed
    started, imported = Event(), Event()
    with pg_app.app_context():
        seed(db.session)
    original = analysis.evaluate
    def pause(*args, **kwargs):
        started.set()
        if not imported.wait(timeout=8):
            raise TimeoutError('import blocked while calculation was in progress')
        return original(*args, **kwargs)
    def calculate_in_thread():
        with pg_app.app_context():
            try:
                analysis.calculate(db.session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
            except ValueError as exc:
                return str(exc)
            finally:
                db.session.remove()
    with patch.object(analysis, 'evaluate', side_effect=pause), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(calculate_in_thread)
        assert started.wait(timeout=8)
        with pg_app.app_context():
            new = payload(17)
            new['accounts'] = []
            new['observations'][0]['observed_at'] = '2026-11-16T12:00:00+08:00'
            import_batch(db.session, new, actor_id=1, idempotency_key='during-analysis')
            imported.set()
        assert '变化' in future.result(timeout=10)
    with pg_app.app_context():
        assert db.session.get(State, 1).published_run_id is None
        assert db.session.query(DailyMetric).count() == 0
