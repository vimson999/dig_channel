import io
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import db
from models.book_commerce import State
from services.book_commerce.analysis import calculate
from services.book_commerce.ingest import import_batch
from test_book_commerce_analysis import seed
from test_book_commerce_ingest import commerce_payload
from apis.v1.book_commerce import bp


@pytest.fixture
def env(monkeypatch):
    from datetime import datetime, timezone
    from services.book_commerce import ingest
    monkeypatch.setattr(ingest, '_utcnow', lambda: datetime(2026, 11, 19, tzinfo=timezone.utc))
    app = Flask(__name__)
    app.config.update(TESTING=True, ENABLE_BOOK_COMMERCE=True, SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False, SECRET_KEY='testing')
    db.init_app(app)
    app.register_blueprint(bp)
    with app.app_context():
        db.create_all()
        yield app.test_client(), db.session
        db.session.remove()
        db.drop_all()


def upload(client, path, payload, **fields):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    return client.post('/api/v1/book-commerce/' + path, data={'file': (io.BytesIO(raw), 'data.json'), **fields}, content_type='multipart/form-data')


def test_admin_preview_commit_and_view_accounts(env):
    client, session = env
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        preview = upload(client, 'observations/preview', commerce_payload())
        assert preview.status_code == 200
        data = preview.get_json()['data']
        committed = upload(client, 'observations/commit', commerce_payload(), payload_hash=data['payload_hash'], expected_revision=str(data['input_revision']), idempotency_key='abc')
        assert committed.status_code == 200
        accounts = client.get('/api/v1/book-commerce/accounts').get_json()['data']['items']
        assert accounts[0]['name'] == '账号'


def test_non_admin_cannot_import(env):
    client, _ = env
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=2, is_admin=False)):
        assert upload(client, 'observations/preview', commerce_payload()).status_code == 403


def test_candidate_decisions_are_private_and_survive_recalculation(env):
    client, session = env
    seed(session)
    run = calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        candidate = client.get('/api/v1/book-commerce/recommendations').get_json()['data']['items'][0]
        response = client.put('/api/v1/book-commerce/recommendations/' + candidate['id'] + '/decision', json={'decision': 'saved', 'expected_version': 0, 'note': '关注'})
        assert response.status_code == 200
        assert client.get('/api/v1/book-commerce/decisions').get_json()['data']['items'][0]['decision'] == 'saved'
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=2, is_admin=False)):
        assert client.get('/api/v1/book-commerce/decisions').get_json()['data']['items'] == []


def test_product_ranking_separates_platform_scope_and_account_scope(env):
    import copy
    client, session = env
    data = commerce_payload()
    data['observations'][0]['scope'] = 'platform_listing'
    import_batch(session, data, actor_id=1, idempotency_key='p1')
    later = copy.deepcopy(data)
    later['video_listing_links'] = []
    for group in ('accounts', 'books', 'listings', 'videos', 'account_listings', 'observations'):
        for item in later[group]:
            item['observed_at'] = '2026-11-18T12:00:00+08:00'
    later['observations'][0]['values']['cumulative_sales'] = 335
    import_batch(session, later, actor_id=1, idempotency_key='p2')
    calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        account_scope = client.get('/api/v1/book-commerce/rankings/products?scope=account_listing').get_json()['data']
        platform_scope = client.get('/api/v1/book-commerce/rankings/products?scope=platform_listing').get_json()['data']
    assert account_scope['total'] == 0
    assert platform_scope['items'][0]['sales_delta'] == '25'


def test_account_list_includes_latest_snapshot_with_observation_date(env):
    client, session = env
    from test_book_commerce_ingest import payload
    data = payload()
    data['observations'][0]['values'] = {'total_posts': 42, 'total_sales': 335}
    data['observations'][0]['quality'] = {'total_posts': 'exact', 'total_sales': 'exact'}
    import_batch(session, data, actor_id=1, idempotency_key='one')
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        account = client.get('/api/v1/book-commerce/accounts').get_json()['data']['items'][0]
    assert account['latest_snapshot']['values']['total_sales'] == '335'
    assert account['latest_snapshot']['business_date'] == '2026-11-18'


def test_ignored_candidates_hidden_by_default_and_available_via_filter(env):
    client, session = env
    seed(session)
    calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        row = client.get('/api/v1/book-commerce/recommendations').get_json()['data']['items'][0]
        client.put('/api/v1/book-commerce/recommendations/' + row['id'] + '/decision', json={'decision':'dismissed', 'expected_version':0})
        assert client.get('/api/v1/book-commerce/recommendations').get_json()['data']['total'] == 0
        assert client.get('/api/v1/book-commerce/recommendations?decision=dismissed').get_json()['data']['total'] == 1


def test_rule_changes_are_versioned_and_mark_previous_analysis_stale(env):
    client, session = env
    seed(session)
    calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        current = client.get('/api/v1/book-commerce/rules').get_json()['data']
        response = client.patch('/api/v1/book-commerce/rules', json={'expected_version': current['version'], 'shares_threshold': 200, 'source_priority': []})
        assert response.status_code == 200
        assert client.get('/api/v1/book-commerce/dashboard').get_json()['data']['is_stale'] is True
        assert client.patch('/api/v1/book-commerce/rules', json={'expected_version': current['version'], 'shares_threshold': 100}).status_code == 409


def test_analysis_request_queues_and_returns_pending_run(env):
    client, session = env
    seed(session)
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)), \
         patch('apis.v1.book_commerce.enqueue_analysis') as enqueue:
        response = client.post('/api/v1/book-commerce/analysis-runs', json={'start_date':'2026-11-18', 'end_date':'2026-11-18'}, headers={'Idempotency-Key':'analysis-one'})
    assert response.status_code == 202
    assert response.get_json()['data']['status'] == 'pending'
    enqueue.assert_called_once()


def test_large_ranking_counts_are_returned_as_decimal_strings(env):
    from models.book_commerce import DailyMetric
    client, session = env
    seed(session)
    run = calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    metric = session.query(DailyMetric).filter_by(generation_id=run.id, kind='showcase').one()
    metric.sales_delta = 9007199254740993
    metric.deltas = {'cumulative_sales': {'delta': 9007199254740993, 'signed_delta': 9007199254740993, 'eligible': True, 'reason': None}}
    session.commit()
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        item = client.get('/api/v1/book-commerce/rankings/products').get_json()['data']['items'][0]
    assert item['sales_delta'] == '9007199254740993'
    assert item['deltas']['cumulative_sales']['delta'] == '9007199254740993'


def test_disabled_feature_exposes_no_data_or_mutation_endpoints(env):
    client, session = env
    app = client.application
    app.config['ENABLE_BOOK_COMMERCE'] = False
    try:
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
            assert client.get('/api/v1/book-commerce/availability').get_json()['data']['enabled'] is False
            assert client.get('/api/v1/book-commerce/accounts').status_code == 404
            assert upload(client, 'observations/commit', commerce_payload()).status_code == 404
        assert session.query(State).count() == 0
    finally:
        app.config['ENABLE_BOOK_COMMERCE'] = True


@pytest.mark.parametrize('missing_table', ['bc_categories', 'bc_account_categories'])
def test_availability_requires_track_tables(env, missing_table):
    from sqlalchemy import text
    client, session = env
    session.add(State(id=1, input_revision=0, active_rules_version=1, rules_config={}))
    session.commit()
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
        assert client.get('/api/v1/book-commerce/availability').get_json()['data']['ready'] is True
        session.execute(text('DROP TABLE ' + missing_table))
        session.commit()
        assert client.get('/api/v1/book-commerce/availability').get_json()['data']['ready'] is False


def test_legacy_evidence_with_credentials_is_not_returned(env):
    from models.book_commerce import VideoListingLink
    client, session = env
    import_batch(session, commerce_payload(), actor_id=1, idempotency_key='legacy')
    session.query(VideoListingLink).one().evidence_reference = 'https://example.com/?access_token=TEST_ONLY'
    session.commit()
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=2, is_admin=False)):
        response = client.get('/api/v1/book-commerce/video-listing-links')
    assert response.status_code == 200
    assert response.get_json()['data']['items'][0]['evidence_reference'] is None


def test_account_history_returns_observations_without_raw_payload(env):
    client, session = env
    from test_book_commerce_ingest import payload
    from models.book_commerce import Account
    import_batch(session, payload(), actor_id=1, idempotency_key='history')
    account_id = session.query(Account).one().id
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=2, is_admin=False)):
        response = client.get('/api/v1/book-commerce/accounts/' + account_id + '/history')
        assert response.status_code == 200
        row = response.get_json()['data']['items'][0]
        assert row['values']['total_posts'] == '335'
        assert 'raw_evidence' not in row


def test_new_analysis_marks_abandoned_run_failed(env):
    from datetime import datetime, timedelta
    from models.book_commerce import AnalysisRun
    client, session = env
    seed(session)
    state = session.get(State, 1)
    old = AnalysisRun(start_date=date(2026, 11, 18), end_date=date(2026, 11, 18), status='running',
                      input_revision=state.input_revision, rules_version=state.active_rules_version,
                      rules_config={}, calculation_version='1', requested_by=1,
                      started_at=datetime.utcnow() - timedelta(minutes=5))
    session.add(old)
    session.commit()
    old_id = old.id
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)), \
         patch('apis.v1.book_commerce.enqueue_analysis'):
        response = client.post('/api/v1/book-commerce/analysis-runs', json={'start_date':'2026-11-18', 'end_date':'2026-11-18'}, headers={'Idempotency-Key':'next'})
    assert response.status_code == 202
    session.expire_all()
    assert session.get(AnalysisRun, old_id).status == 'failed'
    assert session.get(AnalysisRun, old_id).error_code == 'worker_lost'


def test_repeated_abandoned_run_is_reported_failed_and_persisted(env):
    from datetime import datetime, timedelta
    from models.book_commerce import AnalysisRun
    client, session = env
    seed(session)
    state = session.get(State, 1)
    old = AnalysisRun(start_date=date(2026, 11, 18), end_date=date(2026, 11, 18), status='running',
                      input_revision=state.input_revision, rules_version=state.active_rules_version,
                      rules_config={}, calculation_version='1', requested_by=1, request_key='old',
                      started_at=datetime.utcnow() - timedelta(minutes=5))
    session.add(old)
    session.commit()
    old_id = old.id
    with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)), \
         patch('apis.v1.book_commerce.enqueue_analysis'):
        result = client.post('/api/v1/book-commerce/analysis-runs', json={'start_date':'2026-11-18', 'end_date':'2026-11-18'}, headers={'Idempotency-Key':'old'})
    session.rollback()
    session.expire_all()
    assert result.get_json()['data']['status'] == 'failed'
    assert session.get(AnalysisRun, old_id).status == 'failed'


def test_candidate_listing_avoids_one_query_per_related_entity(env):
    from sqlalchemy import event
    client, session = env
    seed(session)
    calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    queries = []
    engine = session.get_bind()
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)):
            response = client.get('/api/v1/book-commerce/recommendations')
        assert response.status_code == 200
        assert response.get_json()['data']['items'][0]['video_title'] == '测试视频'
        assert len(queries) <= 5
    finally:
        event.remove(engine, 'before_cursor_execute', record)
