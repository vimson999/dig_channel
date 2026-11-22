"""Create versioned daily projections and evidence-backed candidates."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from time import monotonic

from sqlalchemy import func, text

from models.book_commerce import (AnalysisRun, DailyMetric, Observation, Recommendation,
                                  State, Video, VideoListingLink)
from services.book_commerce.metrics import daily_delta
from services.book_commerce.recommendations import evaluate


CALCULATION_VERSION = '1'
MAX_CALCULATION_SECONDS = 120
RUN_LEASE_SECONDS = 150


class RunInProgress(RuntimeError):
    pass


def _check_deadline(deadline):
    if monotonic() > deadline:
        raise TimeoutError('分析超时')


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None else None


def _count(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def calculate(session, start_date, end_date, *, actor_id):
    if end_date < start_date or (end_date - start_date).days >= 31:
        raise ValueError('计算区间须为1至31天')
    try:
        return _calculate(session, start_date, end_date, actor_id=actor_id)
    except Exception:
        session.rollback()
        raise


def _calculate(session, start_date, end_date, *, actor_id, run=None):
    deadline = monotonic() + MAX_CALCULATION_SECONDS
    if session.get_bind().dialect.name == 'postgresql':
        session.execute(text("SET LOCAL statement_timeout = '120000'"))
    state = session.query(State).filter_by(id=1).first()
    if state is None:
        raise ValueError('没有可分析的数据')
    revision, rules_version = state.input_revision, state.active_rules_version
    rules = dict(state.rules_config or {})
    if run is None:
        run = AnalysisRun(start_date=start_date, end_date=end_date, status='running', input_revision=revision,
                          rules_version=rules_version, rules_config=rules, calculation_version=CALCULATION_VERSION,
                          requested_by=actor_id, started_at=datetime.utcnow())
        session.add(run)
        session.flush()
    elif run.input_revision != revision or run.rules_version != rules_version:
        raise ValueError('分析期间输入已变化，请重试')
    else:
        rules = dict(run.rules_config or {})
    rows = (session.query(Observation).filter(Observation.business_date >= start_date - timedelta(days=1),
                                               Observation.business_date <= end_date)
            .order_by(Observation.observed_at, Observation.id).limit(100001).all())
    if len(rows) > 100000:
        raise ValueError('观测超过100000条，请缩短计算范围')
    days = {}
    observations_by_id = {}
    series_by_target = defaultdict(set)
    for row in rows:
        _check_deadline(deadline)
        days[(row.target_key, row.series_key, row.business_date)] = row
        observations_by_id[row.id] = row
        series_by_target[row.target_key].add(row.series_key)
    first_seen = {}
    targets = list(series_by_target)
    for offset in range(0, len(targets), 500):
        _check_deadline(deadline)
        group = targets[offset:offset + 500]
        history = (session.query(Observation.target_key, Observation.series_key, func.min(Observation.business_date))
                   .filter(Observation.target_key.in_(group), Observation.business_date <= end_date)
                   .group_by(Observation.target_key, Observation.series_key).all())
        first_seen.update({(target, series): first_date for target, series, first_date in history})
    chosen = {}
    priority = rules.get('source_priority', [])
    for target, series_set in series_by_target.items():
        if len(series_set) == 1:
            chosen[target] = next(iter(series_set))
        else:
            for source in priority:
                matches = [value for value in series_set if value.startswith(source + ':')]
                if len(matches) == 1:
                    chosen[target] = matches[0]
                    break
    projections = []
    for (target, series, day), current in days.items():
        _check_deadline(deadline)
        if day < start_date:
            continue
        previous = days.get((target, series, day - timedelta(days=1)))
        fields = {'showcase': ['cumulative_sales'], 'video': ['likes', 'shares', 'comments'], 'account': []}[current.kind]
        deltas, quality = {}, {}
        for field in fields:
            now = {'observed_at': _aware(current.observed_at), 'value': _count((current.values or {}).get(field)),
                   'quality': (current.quality or {}).get(field), 'series': series}
            before = None if previous is None else {'observed_at': _aware(previous.observed_at),
                'value': _count((previous.values or {}).get(field)), 'quality': (previous.quality or {}).get(field), 'series': series}
            calculated = daily_delta(before, now)
            if before is None and first_seen.get((target, series), day) < day:
                calculated['reason'] = 'missing_previous_day'
            deltas[field] = calculated
            quality[field] = calculated['reason']
        metric = DailyMetric(generation_id=run.id, kind=current.kind, target_key=target, series_key=series,
                             business_date=day, selected_series=(chosen.get(target) == series), account_id=current.account_id if current.metric_scope != 'platform_listing' else None,
                             account_listing_id=current.account_listing_id if current.metric_scope != 'platform_listing' else None,
                             listing_id=current.listing_id, video_id=current.video_id,
                             selected_observation_id=current.id, previous_observation_id=previous.id if previous else None,
                             current_values=current.values, deltas=deltas, quality=quality,
                             window_start=previous.observed_at if previous else None, window_end=current.observed_at,
                             sales_delta=deltas.get('cumulative_sales', {}).get('delta') if deltas.get('cumulative_sales', {}).get('eligible') else None,
                             shares_delta=deltas.get('shares', {}).get('delta') if deltas.get('shares', {}).get('eligible') else None,
                             calculation_version=CALCULATION_VERSION)
        session.add(metric)
        projections.append((current, metric))
    session.flush()
    sales_by_key = defaultdict(list)
    video_by_key = defaultdict(lambda: defaultdict(list))
    for raw, metric in projections:
        if not metric.selected_series:
            continue
        if raw.kind == 'showcase' and raw.metric_scope == 'account_listing' and metric.sales_delta is not None:
            sales_by_key[(raw.account_listing_id, raw.business_date)].append((raw, metric))
        elif raw.kind == 'video' and metric.shares_delta is not None:
            video_by_key[raw.video_id][raw.business_date].append((raw, metric))
    video_ids = list(video_by_key)
    links, videos = [], {}
    for offset in range(0, len(video_ids), 500):
        _check_deadline(deadline)
        group = video_ids[offset:offset + 500]
        links.extend(session.query(VideoListingLink).filter(VideoListingLink.video_id.in_(group)).limit(100001 - len(links)).all())
        if len(links) > 100000:
            raise ValueError('关联数量超过100000条，请缩短范围')
        videos.update({row.id: row for row in session.query(Video).filter(Video.id.in_(group)).all()})
    recommendations = []
    for link in links:
        _check_deadline(deadline)
        video = videos.get(link.video_id)
        if video is None:
            continue
        for day, video_rows in video_by_key.get(link.video_id, {}).items():
            for raw_video, video_metric in video_rows:
                for raw_sales, sale_metric in sales_by_key.get((link.account_listing_id, day), []):
                    sales = {'eligible': True, 'delta': sale_metric.sales_delta, 'scope': raw_sales.metric_scope,
                             'account_id': raw_sales.account_id, 'start': _aware(sale_metric.window_start), 'end': _aware(sale_metric.window_end)}
                    shares = {'eligible': True, 'delta': video_metric.shares_delta, 'account_id': raw_video.account_id,
                              'start': _aware(video_metric.window_start), 'end': _aware(video_metric.window_end)}
                    relation = {'account_id': video.account_id, 'relation_type': link.relation_type,
                                'verification_status': link.verification_status, 'valid_from': _aware(link.valid_from), 'valid_to': _aware(link.valid_to)}
                    eligible, reasons = evaluate(sales, shares, relation, _aware(video.published_at), share_threshold=rules.get('shares_threshold', 100))
                    if not eligible:
                        continue
                    def endpoint(observation_id, field):
                        row = observations_by_id[observation_id]
                        return {'observation_id': row.id, 'observed_at': row.observed_at.isoformat() + 'Z',
                                'value': (row.values or {}).get(field), 'quality': (row.quality or {}).get(field)}
                    evidence = {'sales': {'previous': endpoint(sale_metric.previous_observation_id, 'cumulative_sales'),
                                          'current': endpoint(sale_metric.selected_observation_id, 'cumulative_sales')},
                                'shares': {'previous': endpoint(video_metric.previous_observation_id, 'shares'),
                                           'current': endpoint(video_metric.selected_observation_id, 'shares')},
                                'sales_metric_id': sale_metric.id, 'video_metric_id': video_metric.id,
                                'sales_observation_id': sale_metric.selected_observation_id, 'sales_previous_observation_id': sale_metric.previous_observation_id,
                                'video_observation_id': video_metric.selected_observation_id, 'video_previous_observation_id': video_metric.previous_observation_id,
                                'link_id': link.id, 'sales_series': sale_metric.series_key, 'video_series': video_metric.series_key,
                                'scope': raw_sales.metric_scope, 'message': '视频互动与关联商品销量同期增长，建议关注；不能证明成交归因。'}
                    candidate = Recommendation(run_id=run.id, account_id=raw_video.account_id, account_listing_id=link.account_listing_id,
                                               video_id=video.id, business_date=day, status='candidate', reason_codes=reasons,
                                               evidence=evidence, sales_delta=sale_metric.sales_delta, shares_delta=video_metric.shares_delta,
                                               window_start=min(sale_metric.window_start, video_metric.window_start),
                                               window_end=max(sale_metric.window_end, video_metric.window_end), rule_version=rules_version)
                    session.add(candidate)
                    recommendations.append(candidate)
    competing = defaultdict(set)
    for candidate in recommendations:
        competing[(candidate.account_listing_id, candidate.business_date)].add(candidate.video_id)
    for candidate in recommendations:
        candidate.evidence['competing_video_count'] = len(competing[(candidate.account_listing_id, candidate.business_date)]) - 1
    _check_deadline(deadline)
    state = session.query(State).filter_by(id=1).populate_existing().with_for_update().one()
    run = session.query(AnalysisRun).filter_by(id=run.id).populate_existing().with_for_update().one()
    if run.status != 'running':
        raise ValueError('分析任务状态已变化，请重试')
    if state.input_revision != revision or state.active_rules_version != rules_version:
        raise ValueError('分析期间输入已变化，请重试')
    run.status, run.completed_at = 'completed', datetime.utcnow()
    state.published_run_id = run.id
    session.commit()
    return run


def execute_pending(session, run_id):
    claimed = (session.query(AnalysisRun).filter_by(id=run_id, status='pending')
               .update({'status': 'running', 'started_at': datetime.utcnow(), 'error_code': None}, synchronize_session=False))
    session.commit()
    if not claimed:
        run = session.query(AnalysisRun).filter_by(id=run_id).with_for_update().first()
        if run and run.status == 'running':
            if run.started_at and run.started_at <= datetime.utcnow() - timedelta(seconds=RUN_LEASE_SECONDS):
                run.status = 'failed'
                run.error_code = 'worker_lost'
                run.completed_at = datetime.utcnow()
                session.commit()
                return False
            session.rollback()
            raise RunInProgress('任务仍在运行')
        session.rollback()
        return False
    try:
        run = session.get(AnalysisRun, run_id)
        _calculate(session, run.start_date, run.end_date, actor_id=run.requested_by, run=run)
        return True
    except Exception as exc:
        session.rollback()
        run = session.get(AnalysisRun, run_id)
        if run and run.status == 'running':
            run.status = 'failed'
            run.error_code = 'stale_input' if '变化' in str(exc) else 'calculation_timeout' if isinstance(exc, TimeoutError) else 'calculation_failed'
            run.completed_at = datetime.utcnow()
            session.commit()
        raise
