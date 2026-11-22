"""Authenticated book commerce intelligence endpoints."""
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from functools import wraps
from uuid import UUID

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import BigInteger, and_, or_
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from models import db
from models.book_commerce import (Account, AccountCategory, AccountListing, AnalysisRun, Book,
                                  CandidateDecision, DailyMetric, IngestBatch, Listing, Observation,
                                  Recommendation, State, TrackCategory, Video, VideoListingLink)
from services.book_commerce.analysis import CALCULATION_VERSION, RUN_LEASE_SECONDS
from services.book_commerce.ingest import ImportConflict, _url, import_batch, preview_batch
from utils.auth_utils import require_admin, require_login


bp = Blueprint('book_commerce', __name__, url_prefix='/api/v1/book-commerce')
logger = logging.getLogger(__name__)


@bp.before_request
def enforce_feature_flag():
    if request.endpoint != 'book_commerce.availability' and not current_app.config.get('ENABLE_BOOK_COMMERCE', False):
        return jsonify(success=False, code='feature_disabled', message='图书带货情报尚未启用'), 404


@bp.get('/availability')
@require_login
def availability(_user):
    enabled = bool(current_app.config.get('ENABLE_BOOK_COMMERCE', False))
    if not enabled:
        return jsonify(success=True, data={'enabled': False, 'ready': False})
    try:
        ready = db.session.get(State, 1) is not None
        if ready:
            for model in (TrackCategory, AccountCategory):
                db.session.query(model).limit(1).all()
    except (OperationalError, ProgrammingError):
        db.session.rollback()
        ready = False
    return jsonify(success=True, data={'enabled': True, 'ready': ready})


def guard(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ImportConflict as exc:
            db.session.rollback()
            return jsonify(success=False, code='conflict', message=str(exc)), 409
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            return jsonify(success=False, code='invalid_input', message=str(exc)), 422
        except IntegrityError:
            db.session.rollback()
            return jsonify(success=False, code='conflict', message='数据冲突，请刷新后重试'), 409
        except (OperationalError, ProgrammingError):
            db.session.rollback()
            logger.exception('book commerce storage unavailable')
            return jsonify(success=False, code='schema_not_ready', message='数据存储尚未就绪'), 503
        except Exception:
            db.session.rollback()
            logger.exception('book commerce operation failed')
            return jsonify(success=False, code='internal_error', message='操作失败，请查看服务日志'), 500
    return wrapped


def _upload():
    file = request.files.get('file')
    if file is None:
        raise ValueError('请选择数据文件')
    raw = file.stream.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError('文件超过5 MiB')
    try:
        value = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('文件不是有效的UTF-8 JSON') from None
    return value


def _serialize(row, omit=()):
    result = {}
    for column in row.__table__.columns:
        if column.name in omit:
            continue
        value = getattr(row, column.name)
        if column.name in ('source_url', 'evidence_reference') and value is not None:
            try:
                value = _url(value)
            except ValueError:
                value = None
        if hasattr(value, 'isoformat'):
            value = value.isoformat() + ('Z' if column.type.__class__.__name__ == 'DateTime' else '')
        elif isinstance(column.type, BigInteger) and value is not None:
            value = str(value)
        elif column.name == 'deltas' and isinstance(value, dict):
            value = {key: {k: str(v) if k in ('delta', 'signed_delta') and type(v) is int else v
                           for k, v in fields.items()} for key, fields in value.items()}
        result[column.name] = value
    return result


def _page(query):
    try:
        page, size = int(request.args.get('page', 1)), int(request.args.get('page_size', 20))
    except ValueError:
        raise ValueError('分页参数无效') from None
    if page < 1 or not 1 <= size <= 100:
        raise ValueError('分页参数无效')
    total = query.count()
    return query.offset((page - 1) * size).limit(size).all(), total, page, size


def _items(query, omit=()):
    rows, total, page, size = _page(query)
    return {'items': [_serialize(row, omit) for row in rows], 'total': total, 'page': page, 'page_size': size}


def _category_filter():
    values = request.args.getlist('category_id')
    if len(values) > 50:
        raise ValueError('一次最多选择50个赛道')
    category_ids = []
    for value in values:
        try:
            category_id = str(UUID(value.strip()))
        except (AttributeError, TypeError, ValueError):
            raise ValueError('赛道选项无效') from None
        if category_id not in category_ids:
            category_ids.append(category_id)

    # Accept the previous single-path query during a rolling browser refresh.
    legacy_path = request.args.get('category', '').strip()
    if legacy_path and not category_ids:
        match = db.session.query(TrackCategory.id).filter_by(path=legacy_path).first()
        if match is None:
            raise ValueError('所选赛道不存在，请刷新选项')
        category_ids.append(match[0])
    if category_ids:
        found = {row[0] for row in db.session.query(TrackCategory.id).filter(TrackCategory.id.in_(category_ids)).all()}
        if found != set(category_ids):
            raise ValueError('赛道选项已变化，请刷新后重试')
    return category_ids


def _category_tree_ids(category_ids):
    if not category_ids:
        return None
    paths = [row[0] for row in db.session.query(TrackCategory.path).filter(TrackCategory.id.in_(category_ids)).all()]
    conditions = []
    for path in paths:
        escaped = path.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.extend((TrackCategory.path == path, TrackCategory.path.like(escaped + ' / %', escape='\\')))
    return db.session.query(TrackCategory.id).filter(or_(*conditions))


def _category_accounts(category_ids):
    category_tree = _category_tree_ids(category_ids)
    if category_tree is None:
        return None
    return (db.session.query(AccountCategory.account_id)
            .filter(AccountCategory.category_id.in_(category_tree)).distinct())


def _account_categories_by_account(account_ids):
    account_ids = set(account_ids)
    if not account_ids:
        return {}
    rows = (db.session.query(AccountCategory.account_id, TrackCategory.id, TrackCategory.name,
                             TrackCategory.path, TrackCategory.parent_id)
            .join(TrackCategory, TrackCategory.id == AccountCategory.category_id)
            .filter(AccountCategory.account_id.in_(account_ids)).order_by(TrackCategory.path).all())
    result = {}
    for account_id, category_id, name, path, parent_id in rows:
        result.setdefault(account_id, []).append({'id': category_id, 'name': name, 'path': path, 'parent_id': parent_id})
    return result


def _run():
    state = db.session.get(State, 1)
    run = db.session.get(AnalysisRun, state.published_run_id) if state and state.published_run_id else None
    return state, run


def _context(state, run):
    return {'run_id': run.id if run else None, 'start_date': run.start_date.isoformat() if run else None,
            'end_date': run.end_date.isoformat() if run else None,
            'computed_at': run.completed_at.isoformat() + 'Z' if run and run.completed_at else None,
            'is_stale': bool(run and state and run.input_revision != state.input_revision)}


@bp.post('/observations/preview')
@require_admin
@guard
def preview(user):
    return jsonify(success=True, data=preview_batch(db.session, _upload(), actor_id=user.id))


@bp.post('/observations/commit')
@require_admin
@guard
def commit(user):
    payload = _upload()
    from services.book_commerce.ingest import _digest
    if request.form.get('payload_hash') != _digest(payload):
        raise ValueError('文件与预览不一致，请重新预览')
    try:
        revision = int(request.form.get('expected_revision', ''))
    except ValueError:
        raise ValueError('缺少预览版本') from None
    result = import_batch(db.session, payload, actor_id=user.id,
                          idempotency_key=request.form.get('idempotency_key') or request.headers.get('Idempotency-Key'),
                          expected_revision=revision)
    return jsonify(success=True, data=result)


MODELS = {'accounts': Account, 'books': Book, 'listings': Listing, 'videos': Video,
          'account-listings': AccountListing, 'video-listing-links': VideoListingLink}


@bp.get('/<resource>')
@require_login
@guard
def list_resource(_user, resource):
    model = MODELS.get(resource)
    if model is None:
        return jsonify(success=False, code='not_found', message='不存在'), 404
    query = db.session.query(model)
    category_ids = _category_filter()
    if resource == 'accounts' and category_ids:
        query = query.filter(Account.id.in_(_category_accounts(category_ids)))
    if resource == 'videos' and request.args.get('account_id'):
        query = query.filter_by(account_id=request.args['account_id'])
    term = request.args.get('q', '').strip()
    if len(term) > 100:
        raise ValueError('搜索词过长')
    if term and hasattr(model, 'name'):
        query = query.filter(model.name.ilike('%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%', escape='\\'))
    elif term and hasattr(model, 'title'):
        query = query.filter(model.title.ilike('%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%', escape='\\'))
    rows, total, page, size = _page(query.order_by(model.id))
    items = [_serialize(row, omit=('script_text', 'category') if resource == 'accounts' else ('script_text',)) for row in rows]
    if resource == 'accounts' and rows:
        ids = [row.id for row in rows]
        ranked = (db.session.query(Observation.id.label('observation_id'),
                  db.func.row_number().over(partition_by=Observation.account_id,
                      order_by=(Observation.observed_at.desc(), Observation.id.desc())).label('position'))
                  .filter(Observation.kind == 'account', Observation.account_id.in_(ids)).subquery())
        observations = (db.session.query(Observation).join(ranked, ranked.c.observation_id == Observation.id)
                        .filter(ranked.c.position == 1).all())
        latest = {observation.account_id: observation for observation in observations}
        categories = _account_categories_by_account(ids)
        for item in items:
            found = latest.get(item['id'])
            item['latest_snapshot'] = _serialize(found, omit=('raw_evidence',)) if found else None
            item['categories'] = categories.get(item['id'], [])
    return jsonify(success=True, data={'items': items, 'total': total, 'page': page, 'page_size': size})


@bp.get('/<resource>/<identifier>')
@require_login
@guard
def detail(_user, resource, identifier):
    model = MODELS.get(resource)
    row = db.session.get(model, identifier) if model else None
    if row is None:
        return jsonify(success=False, code='not_found', message='不存在'), 404
    return jsonify(success=True, data=_serialize(row))


@bp.get('/batches')
@require_admin
@guard
def batches(_user):
    return jsonify(success=True, data=_items(db.session.query(IngestBatch).order_by(IngestBatch.received_at.desc(), IngestBatch.id), omit=('source_snapshot',)))


@bp.get('/batches/<identifier>')
@require_admin
@guard
def batch_detail(_user, identifier):
    row = db.session.get(IngestBatch, identifier)
    if row is None:
        return jsonify(success=False, code='not_found', message='不存在'), 404
    return jsonify(success=True, data=_serialize(row))


@bp.get('/dashboard')
@require_login
@guard
def dashboard(_user):
    state, run = _run()
    latest = db.session.query(db.func.max(Observation.business_date)).scalar()
    return jsonify(success=True, data={'account_count': db.session.query(Account).count(),
                                      'listing_count': db.session.query(Listing).count(),
                                      'video_count': db.session.query(Video).count(),
                                      'latest_observed_date': latest.isoformat() if latest else None,
                                      **_context(state, run)})


@bp.get('/filters')
@require_login
@guard
def filters(_user):
    categories = db.session.query(TrackCategory).order_by(TrackCategory.path).all()
    path_to_id = {row.path: row.id for row in categories}
    category_by_id = {row.id: row for row in categories}
    account_links = db.session.query(AccountCategory.account_id, AccountCategory.category_id).all()
    accounts_by_category = {row.id: set() for row in categories}
    for account_id, leaf_id in account_links:
        leaf = category_by_id.get(leaf_id)
        if leaf is None:
            continue
        parts = leaf.path.split(' / ')
        for depth in range(1, len(parts) + 1):
            ancestor_id = path_to_id.get(' / '.join(parts[:depth]))
            if ancestor_id:
                accounts_by_category[ancestor_id].add(account_id)
    result = [{'id': row.id, 'name': row.name, 'path': row.path, 'parent_id': row.parent_id,
               'depth': row.path.count(' / '), 'account_count': len(accounts_by_category[row.id])}
              for row in categories]
    return jsonify(success=True, data={'categories': result})


def enqueue_analysis(run_id):
    from celery_config import celery_app
    from services.runtime_guard import end_db_transaction_before_external_io
    end_db_transaction_before_external_io('book commerce worker inspection')
    inspector = celery_app.control.inspect(timeout=1.0)
    queues = inspector.active_queues() or {}
    if not any(any(q.get('name') == 'book_commerce' for q in (items or [])) for items in queues.values()):
        raise RuntimeError('图书带货计算服务未启动')
    celery_app.send_task('tasks.book_commerce.calculate', args=[run_id], queue='book_commerce')


@bp.post('/analysis-runs')
@require_admin
@guard
def create_analysis(user):
    data = request.get_json(silent=True) or {}
    if set(data) != {'start_date', 'end_date'}:
        raise ValueError('计算日期无效')
    start, end = date.fromisoformat(data['start_date']), date.fromisoformat(data['end_date'])
    if end < start or (end - start).days >= 31:
        raise ValueError('计算区间须为1至31天')
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > 128:
        raise ValueError('缺少幂等标识')
    expired = datetime.utcnow() - timedelta(seconds=RUN_LEASE_SECONDS)
    expired_count = (db.session.query(AnalysisRun).filter(AnalysisRun.status == 'running', AnalysisRun.started_at < expired)
                     .update({'status': 'failed', 'error_code': 'worker_lost', 'completed_at': datetime.utcnow()}, synchronize_session='fetch'))
    if expired_count:
        db.session.commit()
    prior = db.session.query(AnalysisRun).filter_by(requested_by=user.id, request_key=key).populate_existing().first()
    if prior:
        if prior.start_date != start or prior.end_date != end:
            raise ImportConflict('同一幂等标识对应不同计算范围')
        if prior.status != 'pending' or prior.error_code != 'enqueue_failed':
            return jsonify(success=True, data=_serialize(prior)), 202 if prior.status == 'pending' else 200
        run = prior
    else:
        state = db.session.get(State, 1)
        if state is None:
            raise ValueError('没有可分析的数据')
        run = AnalysisRun(start_date=start, end_date=end, status='pending', input_revision=state.input_revision,
                          rules_version=state.active_rules_version, rules_config=state.rules_config or {},
                          calculation_version=CALCULATION_VERSION, requested_by=user.id, request_key=key)
        db.session.add(run)
        db.session.commit()
    run_id = run.id
    db.session.commit()
    try:
        enqueue_analysis(run_id)
    except Exception:
        logger.exception('book commerce analysis enqueue failed')
        run = db.session.get(AnalysisRun, run_id)
        if run and run.status == 'pending':
            run.error_code = 'enqueue_failed'
            db.session.commit()
        return jsonify(success=False, code='worker_unavailable', message='计算服务未就绪，请稍后重试', run_id=run_id), 503
    run = db.session.get(AnalysisRun, run_id)
    return jsonify(success=True, data=_serialize(run)), 202


@bp.get('/analysis-runs/<identifier>')
@require_login
@guard
def analysis_detail(_user, identifier):
    row = db.session.get(AnalysisRun, identifier)
    if row is None:
        return jsonify(success=False, code='not_found', message='不存在'), 404
    return jsonify(success=True, data=_serialize(row))


@bp.get('/rankings/<kind>')
@require_login
@guard
def rankings(_user, kind):
    if kind not in ('products', 'videos'):
        raise ValueError('榜单类型无效')
    category_ids = _category_filter()
    state, run = _run()
    if not run:
        return jsonify(success=True, data={'items': [], 'total': 0, **_context(state, run)})
    selected = date.fromisoformat(request.args.get('date', run.end_date.isoformat()))
    metric_kind = 'showcase' if kind == 'products' else 'video'
    column = DailyMetric.sales_delta if kind == 'products' else DailyMetric.shares_delta
    query = db.session.query(DailyMetric).filter_by(generation_id=run.id, kind=metric_kind, business_date=selected, selected_series=True).filter(column.isnot(None))
    if kind == 'products':
        scope = request.args.get('scope', 'account_listing')
        if scope not in ('account_listing', 'platform_listing'):
            raise ValueError('销量范围无效')
        query = query.filter(DailyMetric.target_key.like('account_listing:%' if scope == 'account_listing' else 'listing:%'))
    else:
        scope = None
    if category_ids:
        category_accounts = _category_accounts(category_ids)
        if kind == 'products' and scope == 'platform_listing':
            category_listings = (db.session.query(AccountListing.listing_id)
                                 .filter(AccountListing.account_id.in_(category_accounts)))
            query = query.filter(DailyMetric.listing_id.in_(category_listings))
        else:
            query = query.filter(DailyMetric.account_id.in_(category_accounts))
    if request.args.get('account_id'):
        query = query.filter_by(account_id=request.args['account_id'])
    rows, total, page, size = _page(query.order_by(column.desc(), DailyMetric.id))
    account_ids = {row.account_id for row in rows if row.account_id}
    listing_ids = {row.listing_id for row in rows if row.listing_id}
    video_ids = {row.video_id for row in rows if row.video_id}
    accounts = {x.id: x for x in db.session.query(Account).filter(Account.id.in_(account_ids)).all()}
    categories_by_account = _account_categories_by_account(account_ids)
    listings = {x.id: x.title for x in db.session.query(Listing).filter(Listing.id.in_(listing_ids)).all()}
    videos = {x.id: x.title for x in db.session.query(Video).filter(Video.id.in_(video_ids)).all()}
    items = []
    for row in rows:
        item = _serialize(row)
        account = accounts.get(row.account_id)
        item.update(account_name=account.name if account else None,
                    categories=categories_by_account.get(row.account_id, []),
                    listing_title=listings.get(row.listing_id), video_title=videos.get(row.video_id))
        items.append(item)
    result = {'items': items, 'total': total, 'page': page, 'page_size': size}
    result.update(_context(state, run))
    result['date_computed'] = run.start_date <= selected <= run.end_date
    return jsonify(success=True, data=result)


@bp.get('/recommendations')
@require_login
@guard
def recommendations(user):
    category_ids = _category_filter()
    state, run = _run()
    if not run:
        return jsonify(success=True, data={'items': [], 'total': 0, **_context(state, run)})
    query = (db.session.query(Recommendation, CandidateDecision, Video.title, Listing.title, Account.name)
        .join(Video, Video.id == Recommendation.video_id)
        .join(AccountListing, AccountListing.id == Recommendation.account_listing_id)
        .join(Listing, Listing.id == AccountListing.listing_id)
        .join(Account, Account.id == Recommendation.account_id)
        .outerjoin(CandidateDecision, and_(CandidateDecision.owner_user_id == user.id,
            CandidateDecision.video_id == Recommendation.video_id,
            CandidateDecision.account_listing_id == Recommendation.account_listing_id,
            CandidateDecision.business_date == Recommendation.business_date))
        .filter(Recommendation.run_id == run.id, Recommendation.status == 'candidate'))
    if category_ids:
        query = query.filter(Account.id.in_(_category_accounts(category_ids)))
    decision_filter = request.args.get('decision', 'visible')
    if decision_filter == 'visible':
        query = query.filter(or_(CandidateDecision.id.is_(None), CandidateDecision.decision != 'dismissed'))
    elif decision_filter == 'pending':
        query = query.filter(or_(CandidateDecision.id.is_(None), CandidateDecision.decision == 'pending'))
    elif decision_filter in ('saved', 'dismissed', 'to_research'):
        query = query.filter(CandidateDecision.decision == decision_filter)
    elif decision_filter != 'all':
        raise ValueError('决策筛选无效')
    selected = request.args.get('date')
    if selected:
        query = query.filter(Recommendation.business_date == date.fromisoformat(selected))
    rows, total, page, size = _page(query.order_by(Recommendation.shares_delta.desc(), Recommendation.id))
    items = []
    categories_by_account = _account_categories_by_account({row.account_id for row, *_ in rows})
    for row, decision, video_title, listing_title, account_name in rows:
        item = _serialize(row)
        item.update(video_title=video_title, listing_title=listing_title,
                    account_name=account_name, categories=categories_by_account.get(row.account_id, []),
                    decision=decision.decision if decision else 'pending',
                    decision_version=decision.version if decision else 0, note=decision.note if decision else None)
        items.append(item)
    return jsonify(success=True, data={'items': items, 'total': total, 'page': page, 'page_size': size, **_context(state, run)})


@bp.put('/recommendations/<identifier>/decision')
@require_login
@guard
def save_decision(user, identifier):
    rec = db.session.get(Recommendation, identifier)
    if rec is None:
        return jsonify(success=False, code='not_found', message='候选不存在'), 404
    data = request.get_json(silent=True) or {}
    decision, note, version = data.get('decision'), data.get('note'), data.get('expected_version')
    if decision not in ('pending', 'saved', 'dismissed', 'to_research') or type(version) is not int or version < 0:
        raise ValueError('决策状态或版本无效')
    if note is not None and (not isinstance(note, str) or len(note) > 2000):
        raise ValueError('备注无效')
    query = db.session.query(CandidateDecision).filter_by(owner_user_id=user.id, video_id=rec.video_id,
                account_listing_id=rec.account_listing_id, business_date=rec.business_date)
    row = query.with_for_update().first()
    if row is None:
        if version != 0:
            raise ImportConflict('决策已变化')
        row = CandidateDecision(owner_user_id=user.id, video_id=rec.video_id, account_listing_id=rec.account_listing_id,
                                business_date=rec.business_date, decision=decision, note=note, version=1, evidence_run_id=rec.run_id)
        db.session.add(row)
    elif row.decision == decision and row.note == note and row.evidence_run_id == rec.run_id:
        return jsonify(success=True, data=_serialize(row))
    elif row.version != version:
        raise ImportConflict('决策已变化')
    else:
        row.decision, row.note, row.evidence_run_id = decision, note, rec.run_id
        row.version += 1
    db.session.commit()
    return jsonify(success=True, data=_serialize(row))


@bp.get('/decisions')
@require_login
@guard
def decisions(user):
    query = (db.session.query(CandidateDecision).join(AccountListing, AccountListing.id == CandidateDecision.account_listing_id)
             .join(Account, Account.id == AccountListing.account_id).filter(CandidateDecision.owner_user_id == user.id))
    category_ids = _category_filter()
    if category_ids:
        query = query.filter(Account.id.in_(_category_accounts(category_ids)))
    selected = request.args.get('decision')
    if selected:
        if selected not in ('pending', 'saved', 'dismissed', 'to_research'):
            raise ValueError('决策状态无效')
        query = query.filter_by(decision=selected)
    rows, total, page, size = _page(query.order_by(CandidateDecision.updated_at.desc(), CandidateDecision.id))
    state, run = _run()
    items = []
    links = {row.id: row.account_id for row in
             db.session.query(AccountListing.id, AccountListing.account_id)
             .filter(AccountListing.id.in_({item.account_listing_id for item in rows})).all()}
    categories_by_account = _account_categories_by_account(links.values())
    for row in rows:
        record = _serialize(row)
        video = db.session.get(Video, row.video_id)
        link = db.session.get(AccountListing, row.account_listing_id)
        account = db.session.get(Account, link.account_id) if link else None
        listing = db.session.get(Listing, link.listing_id) if link else None
        evidence = db.session.query(Recommendation).filter_by(run_id=row.evidence_run_id, video_id=row.video_id, account_listing_id=row.account_listing_id, business_date=row.business_date).first()
        current = db.session.query(Recommendation).filter_by(run_id=run.id, video_id=row.video_id, account_listing_id=row.account_listing_id, business_date=row.business_date).first() if run else None
        record.update(video_title=video.title if video else None, listing_title=listing.title if listing else None,
                      account_name=account.name if account else None,
                      categories=categories_by_account.get(link.account_id, []) if link else [],
                      recommendation_id=(current or evidence).id if current or evidence else None,
                      current_status=('该日期未重新计算' if not run or not run.start_date <= row.business_date <= run.end_date else '当前候选' if current else '当前规则下未入选'))
        items.append(record)
    return jsonify(success=True, data={'items': items, 'total': total, 'page': page, 'page_size': size, **_context(state, run)})


@bp.get('/rules')
@require_login
@guard
def get_rules(_user):
    state = db.session.get(State, 1)
    config = dict(state.rules_config or {}) if state else {}
    return jsonify(success=True, data={'version': state.active_rules_version if state else 1,
                                       'shares_threshold': config.get('shares_threshold', 100),
                                       'source_priority': config.get('source_priority', [])})


@bp.patch('/rules')
@require_admin
@guard
def set_rules(_user):
    data = request.get_json(silent=True) or {}
    if set(data) - {'expected_version', 'shares_threshold', 'source_priority'}:
        raise ValueError('规则字段无效')
    version, threshold, priority = data.get('expected_version'), data.get('shares_threshold'), data.get('source_priority', [])
    if type(version) is not int or type(threshold) is not int or not 0 <= threshold <= 9223372036854775807:
        raise ValueError('规则阈值或版本无效')
    if not isinstance(priority, list) or len(priority) > 100 or any(not isinstance(s, str) or not s or len(s) > 100 for s in priority) or len(set(priority)) != len(priority):
        raise ValueError('来源优先级无效')
    state = db.session.query(State).filter_by(id=1).with_for_update().first()
    if state is None:
        state = State(id=1, input_revision=0, active_rules_version=1, rules_config={})
        db.session.add(state)
        db.session.flush()
    if state.active_rules_version != version:
        raise ImportConflict('规则已变化，请刷新')
    config = {'shares_threshold': threshold, 'source_priority': priority}
    if config != {'shares_threshold': (state.rules_config or {}).get('shares_threshold', 100),
                  'source_priority': (state.rules_config or {}).get('source_priority', [])}:
        state.rules_config = config
        state.active_rules_version += 1
        state.input_revision += 1
    db.session.commit()
    return jsonify(success=True, data={'version': state.active_rules_version, **config})


@bp.get('/<resource>/<identifier>/history')
@require_login
@guard
def history(_user, resource, identifier):
    resources = {'accounts': (Account, 'account', Observation.account_id),
                 'videos': (Video, 'video', Observation.video_id),
                 'account-listings': (AccountListing, 'showcase', Observation.account_listing_id)}
    if resource not in resources:
        return jsonify(success=False, code='not_found', message='不存在'), 404
    model, kind, column = resources[resource]
    if db.session.get(model, identifier) is None:
        return jsonify(success=False, code='not_found', message='不存在'), 404
    query = (db.session.query(Observation).filter(Observation.kind == kind, column == identifier)
             .order_by(Observation.observed_at.desc(), Observation.id))
    return jsonify(success=True, data=_items(query, omit=('raw_evidence', 'content_hash', 'slot_key', 'batch_id')))
