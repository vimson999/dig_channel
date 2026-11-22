import copy
from datetime import datetime
import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from models import db
from models.book_commerce import Account, Observation, State
from services.book_commerce.ingest import import_batch, ImportConflict


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


def payload(value=335):
    return {'source': 'fixture', 'adapter_version': '1', 'accounts': [{'source_key': 'a', 'name': '账号', 'observed_at': '2026-11-18T12:00:00+08:00'}],
            'observations': [{'kind': 'account', 'account': 'a', 'observed_at': '2026-11-18T12:00:00+08:00',
                              'values': {'total_posts': value}, 'quality': {'total_posts': 'exact'}}]}


def test_import_creates_account_and_raw_observation(session):
    result = import_batch(session, payload(), actor_id=1, idempotency_key='one')
    assert result['created_observations'] == 1
    assert session.query(Account).count() == 1
    assert session.query(Observation).one().values == {'total_posts': '335'}
    assert session.get(State, 1).input_revision == 1


def test_retry_is_idempotent_and_new_key_same_fact_does_not_invalidate(session):
    first = import_batch(session, payload(), actor_id=1, idempotency_key='one')
    again = import_batch(session, payload(), actor_id=1, idempotency_key='one')
    another = import_batch(session, payload(), actor_id=1, idempotency_key='two')
    assert again == first
    assert another['duplicate_observations'] == 1
    assert session.get(State, 1).input_revision == 1


def test_conflict_rolls_back_new_master_data(session):
    import_batch(session, payload(), actor_id=1, idempotency_key='one')
    changed = payload(336)
    changed['accounts'].append({'source_key': 'b', 'name': '其他账号', 'observed_at': '2026-11-18T12:00:00+08:00'})
    with pytest.raises(ImportConflict):
        import_batch(session, changed, actor_id=1, idempotency_key='two')
    assert session.query(Account).count() == 1
    assert session.query(Observation).one().values['total_posts'] == '335'


def commerce_payload():
    return {
        'source': 'fixture', 'adapter_version': '1',
        'accounts': [{'source_key': 'a', 'name': '账号', 'observed_at': '2026-11-17T12:00:00+08:00'}],
        'books': [{'source_key': 'book', 'title': '测试书', 'isbn13': '9787111128069', 'observed_at': '2026-11-17T12:00:00+08:00'}],
        'listings': [{'source_key': 'item', 'title': '测试商品', 'book': 'book', 'observed_at': '2026-11-17T12:00:00+08:00'}],
        'videos': [{'source_key': 'video', 'account': 'a', 'title': '测试视频', 'published_at': '2026-11-15T12:00:00+08:00', 'observed_at': '2026-11-17T12:00:00+08:00'}],
        'account_listings': [{'account': 'a', 'listing': 'item', 'observed_at': '2026-11-17T12:00:00+08:00'}],
        'video_listing_links': [{'video': 'video', 'account': 'a', 'listing': 'item', 'valid_from': '2026-11-16T12:00:00+08:00', 'observed_at': '2026-11-17T12:00:00+08:00', 'relation_type': 'attached', 'verification_status': 'confirmed'}],
        'observations': [
            {'kind': 'showcase', 'account': 'a', 'listing': 'item', 'scope': 'account_listing', 'observed_at': '2026-11-17T12:00:00+08:00', 'values': {'cumulative_sales': 310}, 'quality': {'cumulative_sales': 'exact'}},
            {'kind': 'video', 'account': 'a', 'video': 'video', 'observed_at': '2026-11-17T12:00:00+08:00', 'values': {'likes': 100, 'shares': 200}, 'quality': {'likes': 'exact', 'shares': 'exact'}},
        ],
    }


def test_imports_book_listing_video_link_and_both_observation_types(session):
    from models.book_commerce import Book, Listing, Video, AccountListing, VideoListingLink
    result = import_batch(session, commerce_payload(), actor_id=1, idempotency_key='commerce')
    assert result['created_observations'] == 2
    assert session.query(Book).count() == session.query(Listing).count() == 1
    assert session.query(Video).count() == session.query(AccountListing).count() == session.query(VideoListingLink).count() == 1
    rows = {row.kind: row for row in session.query(Observation)}
    assert rows['showcase'].target_key.startswith('account_listing:')
    assert rows['video'].values['shares'] == '200'


@pytest.mark.parametrize('reference', [
    'https://example.com/?access_token=TEST_ONLY',
    'https://example.com/?X-Amz-Signature=TEST_ONLY',
    'https://user:password@example.com/evidence',
    'javascript:alert(1)',
])
def test_import_rejects_unsafe_evidence_links(session, reference):
    item = commerce_payload()
    item['video_listing_links'][0]['evidence_reference'] = reference
    with pytest.raises(ValueError):
        import_batch(session, item, actor_id=1, idempotency_key='unsafe-evidence')
    assert session.query(Account).count() == 0


def test_cross_account_video_link_rejects_whole_batch(session):
    item = commerce_payload()
    item['accounts'].append({'source_key': 'b', 'name': '别的账号', 'observed_at': '2026-11-17T12:00:00+08:00'})
    item['account_listings'][0]['account'] = 'b'
    item['video_listing_links'][0]['account'] = 'b'
    with pytest.raises(ValueError):
        import_batch(session, item, actor_id=1, idempotency_key='bad')
    assert session.query(Account).count() == 0


def test_platform_listing_observations_across_accounts_do_not_duplicate(session):
    item = commerce_payload()
    item['accounts'].append({'source_key': 'b', 'name': '账号2', 'observed_at': '2026-11-17T12:00:00+08:00'})
    item['account_listings'].append({'account': 'b', 'listing': 'item', 'observed_at': '2026-11-17T12:00:00+08:00'})
    first = item['observations'][0]
    first['scope'] = 'platform_listing'
    second = copy.deepcopy(first)
    second['account'] = 'b'
    item['observations'].append(second)
    result = import_batch(session, item, actor_id=1, idempotency_key='platform')
    assert result['created_observations'] == 2
    assert result['duplicate_observations'] == 1
    assert session.query(Observation).filter_by(kind='showcase').one().target_key.startswith('listing:')


def test_bad_isbn_and_invalid_values_are_rejected(session):
    item = commerce_payload()
    item['books'][0]['isbn13'] = '9787111128068'
    with pytest.raises(ValueError):
        import_batch(session, item, actor_id=1, idempotency_key='bad-isbn')
    item = commerce_payload()
    item['observations'][0]['values']['cumulative_sales'] = -1
    with pytest.raises(ValueError):
        import_batch(session, item, actor_id=1, idempotency_key='bad-count')
    assert session.query(Account).count() == 0


def test_preview_does_not_write_and_changed_revision_rejects_commit(session):
    from services.book_commerce.ingest import preview_batch
    preview = preview_batch(session, commerce_payload(), actor_id=1)
    assert preview['created_observations'] == 2
    assert preview['input_revision'] == 0
    assert session.query(Account).count() == 0
    import_batch(session, payload(), actor_id=1, idempotency_key='other')
    with pytest.raises(ImportConflict):
        import_batch(session, commerce_payload(), actor_id=1, idempotency_key='stale', expected_revision=preview['input_revision'])
    assert session.query(Account).count() == 1


def test_rejected_batch_is_audited_without_committing_business_data(session):
    from models.book_commerce import IngestBatch
    broken = commerce_payload()
    broken['observations'][0]['values']['cumulative_sales'] = -1
    with pytest.raises(ValueError):
        import_batch(session, broken, actor_id=1, idempotency_key='rejected')
    assert session.query(Account).count() == 0
    batch = session.query(IngestBatch).filter_by(idempotency_key='rejected').one()
    assert batch.status == 'rejected'
    assert batch.error_summary['code'] == 'invalid_input'
    with pytest.raises(ImportConflict):
        import_batch(session, broken, actor_id=1, idempotency_key='rejected')


def test_future_observation_is_rejected(session, monkeypatch):
    from datetime import timezone
    from services.book_commerce import ingest
    monkeypatch.setattr(ingest, '_utcnow', lambda: datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='未来'):
        import_batch(session, payload(), actor_id=1, idempotency_key='future')
    assert session.query(Observation).count() == 0


def test_invalid_observation_reports_record_index(session):
    item = commerce_payload()
    item['observations'][0]['values']['cumulative_sales'] = -1
    with pytest.raises(ValueError, match=r'observations\[0\]'):
        import_batch(session, item, actor_id=1, idempotency_key='bad-path')


def test_abandoned_accepted_batch_can_resume_with_same_key(session):
    from datetime import timedelta
    from models.book_commerce import IngestBatch
    from services.book_commerce.ingest import _digest
    data = payload()
    session.add(IngestBatch(source='fixture', adapter_version='1', idempotency_key='abandoned',
                            payload_hash=_digest(data), actor_user_id=1, status='accepted',
                            received_at=datetime.utcnow() - timedelta(minutes=10), attempted_at=datetime.utcnow() - timedelta(minutes=10), attempt_count=1))
    session.commit()
    result = import_batch(session, data, actor_id=1, idempotency_key='abandoned')
    batch = session.query(IngestBatch).filter_by(idempotency_key='abandoned').one()
    assert result['created_observations'] == 1
    assert batch.status == 'committed'
    assert batch.attempt_count == 2


def test_same_timestamp_supplement_fills_previously_missing_description(session):
    first = payload()
    import_batch(session, first, actor_id=1, idempotency_key='first')
    second = payload()
    second['accounts'][0]['category'] = '养生'
    import_batch(session, second, actor_id=1, idempotency_key='second')
    assert session.query(Account).one().category == '养生'


def test_source_url_with_credential_query_is_rejected(session):
    item = payload()
    item['accounts'][0]['source_url'] = 'https://example.com/profile?token=private'
    with pytest.raises(ValueError, match='链接'):
        import_batch(session, item, actor_id=1, idempotency_key='credential-url')
    assert session.query(Account).count() == 0
