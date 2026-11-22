import copy
from datetime import date
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from models import db
from models.book_commerce import DailyMetric, Recommendation, State
from services.book_commerce.ingest import import_batch
from services.book_commerce.analysis import calculate
from test_book_commerce_ingest import commerce_payload


@pytest.fixture
def session(monkeypatch):
    from datetime import datetime, timezone
    from services.book_commerce import ingest
    monkeypatch.setattr(ingest, '_utcnow', lambda: datetime(2026, 11, 19, tzinfo=timezone.utc))
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield db.session
        db.session.remove()
        db.drop_all()


def seed(session):
    first = commerce_payload()
    import_batch(session, first, actor_id=1, idempotency_key='one')
    second = copy.deepcopy(first)
    for group in ('accounts', 'books', 'listings', 'videos', 'account_listings'):
        for item in second[group]:
            item['observed_at'] = '2026-11-18T12:00:00+08:00'
    second['video_listing_links'] = []
    for obs in second['observations']:
        obs['observed_at'] = '2026-11-18T12:00:00+08:00'
        if obs['kind'] == 'video':
            obs['values']['shares'] = 380
        else:
            obs['values']['cumulative_sales'] = 335
    import_batch(session, second, actor_id=1, idempotency_key='two')


def test_calculation_creates_daily_growth_and_recommendation(session):
    seed(session)
    run = calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    assert run.status == 'completed'
    sales = session.query(DailyMetric).filter_by(kind='showcase', generation_id=run.id).one()
    video = session.query(DailyMetric).filter_by(kind='video', generation_id=run.id).one()
    assert sales.sales_delta == 25 and video.shares_delta == 180
    recommendation = session.query(Recommendation).filter_by(run_id=run.id).one()
    assert recommendation.sales_delta == 25 and recommendation.shares_delta == 180
    assert session.get(State, 1).published_run_id == run.id


def test_missing_yesterday_produces_no_daily_growth_or_recommendation(session):
    item = commerce_payload()
    import_batch(session, item, actor_id=1, idempotency_key='one')
    run = calculate(session, date(2026, 11, 17), date(2026, 11, 17), actor_id=1)
    metric = session.query(DailyMetric).filter_by(kind='showcase', generation_id=run.id).one()
    assert metric.sales_delta is None
    assert session.query(Recommendation).filter_by(run_id=run.id).count() == 0


def test_multiple_sources_require_configured_priority_for_ranking(session):
    from models.book_commerce import Account, Observation
    seed(session)
    original = session.query(Observation).filter_by(kind='video').order_by(Observation.observed_at.desc()).first()
    other_account = Account(source='another', source_key='a', platform='wechat_channels', name='other')
    session.add(other_account)
    session.flush()
    clone = Observation(kind='video', account_id=original.account_id, video_id=original.video_id,
                        target_key=original.target_key, series_key='another:video:v1', slot_key='another-slot',
                        observed_at=original.observed_at, business_date=original.business_date,
                        values=original.values, quality=original.quality, content_hash='c' * 64)
    session.add(clone)
    session.commit()
    run = calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    videos = session.query(DailyMetric).filter_by(kind='video', generation_id=run.id).all()
    assert len(videos) == 2
    assert not any(x.selected_series for x in videos)
    assert session.query(Recommendation).filter_by(run_id=run.id).count() == 0


def test_result_is_not_published_if_input_revision_changes_during_calculation(session):
    from unittest.mock import patch
    from services.book_commerce import analysis
    seed(session)
    original = analysis.evaluate
    def change_input(*args, **kwargs):
        state = session.get(State, 1)
        state.input_revision += 1
        session.flush()
        return original(*args, **kwargs)
    with patch.object(analysis, 'evaluate', side_effect=change_input):
        with pytest.raises(ValueError, match='变化'):
            calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    assert session.get(State, 1).published_run_id is None
    assert session.query(DailyMetric).count() == 0


def test_pending_run_claim_is_idempotent(session):
    from models.book_commerce import AnalysisRun
    from services.book_commerce.analysis import execute_pending
    seed(session)
    state = session.get(State, 1)
    run = AnalysisRun(start_date=date(2026, 11, 18), end_date=date(2026, 11, 18), status='pending',
                      input_revision=state.input_revision, rules_version=state.active_rules_version,
                      rules_config={}, calculation_version='1', requested_by=1, request_key='one')
    session.add(run)
    session.commit()
    run_id = run.id
    assert execute_pending(session, run_id) is True
    assert execute_pending(session, run_id) is False
    assert session.get(AnalysisRun, run_id).status == 'completed'
    assert session.query(Recommendation).filter_by(run_id=run_id).count() == 1


def test_pending_run_with_old_input_fails_without_replacing_current(session):
    from models.book_commerce import AnalysisRun
    from services.book_commerce.analysis import execute_pending
    seed(session)
    state = session.get(State, 1)
    run = AnalysisRun(start_date=date(2026, 11, 18), end_date=date(2026, 11, 18), status='pending',
                      input_revision=state.input_revision - 1, rules_version=state.active_rules_version,
                      rules_config={}, calculation_version='1', requested_by=1, request_key='stale')
    session.add(run)
    session.commit()
    run_id = run.id
    with pytest.raises(ValueError, match='变化'):
        execute_pending(session, run_id)
    assert session.get(AnalysisRun, run_id).status == 'failed'
    assert session.get(AnalysisRun, run_id).error_code == 'stale_input'
    assert session.get(State, 1).published_run_id is None


def test_interrupted_running_job_expires_safely_without_publishing(session):
    from datetime import datetime, timedelta
    from models.book_commerce import AnalysisRun
    from services.book_commerce.analysis import execute_pending
    seed(session)
    state = session.get(State, 1)
    run = AnalysisRun(start_date=date(2026, 11, 18), end_date=date(2026, 11, 18), status='running',
                      input_revision=state.input_revision, rules_version=state.active_rules_version,
                      rules_config={}, calculation_version='1', requested_by=1,
                      started_at=datetime.utcnow() - timedelta(minutes=5))
    session.add(run)
    session.commit()
    assert execute_pending(session, run.id) is False
    assert session.get(AnalysisRun, run.id).status == 'failed'
    assert session.get(AnalysisRun, run.id).error_code == 'worker_lost'
    assert session.get(State, 1).published_run_id is None


def test_gap_after_earlier_observation_is_missing_previous_day(session):
    item = commerce_payload()
    import_batch(session, item, actor_id=1, idempotency_key='first')
    later = copy.deepcopy(item)
    for group in ('accounts', 'books', 'listings', 'videos', 'account_listings', 'observations'):
        for record in later[group]:
            record['observed_at'] = '2026-11-19T12:00:00+08:00'
    later['video_listing_links'] = []
    from services.book_commerce import ingest
    from datetime import datetime, timezone
    with patch.object(ingest, '_utcnow', return_value=datetime(2026, 11, 20, tzinfo=timezone.utc)):
        import_batch(session, later, actor_id=1, idempotency_key='later')
    run = calculate(session, date(2026, 11, 19), date(2026, 11, 19), actor_id=1)
    sales = session.query(DailyMetric).filter_by(generation_id=run.id, kind='showcase').one()
    assert sales.sales_delta is None
    assert sales.deltas['cumulative_sales']['reason'] == 'missing_previous_day'


def test_recommendation_explains_observation_endpoints_and_other_candidates(session):
    seed(session)
    run = calculate(session, date(2026, 11, 18), date(2026, 11, 18), actor_id=1)
    rec = session.query(Recommendation).filter_by(run_id=run.id).one()
    assert rec.evidence['sales']['previous']['value'] == '310'
    assert rec.evidence['sales']['current']['value'] == '335'
    assert rec.evidence['shares']['previous']['value'] == '200'
    assert rec.evidence['shares']['current']['value'] == '380'
    assert rec.evidence['competing_video_count'] == 0
