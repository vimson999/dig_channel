"""Validate and atomically store imported observations and their identities."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from models.book_commerce import (Account, AccountCategory, AccountListing, Book, IngestBatch, Listing,
                                  Observation, State, TrackCategory, Video, VideoListingLink)


class ImportConflict(ValueError):
    pass


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    except (AttributeError, TypeError, ValueError):
        raise ValueError('采集时间必须包含时区') from None


def _utcnow():
    return datetime.now(timezone.utc)


def _observed_time(value):
    observed = _time(value)
    if observed > (_utcnow() + timedelta(minutes=5)).replace(tzinfo=None):
        raise ValueError('采集时间不能晚于当前时间超过5分钟（未来时间）')
    return observed


def _text(value, field, limit=255, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{field}无效')
    return value.strip()


def _url(value):
    if value is None:
        return None
    value = _text(value, '链接', 2048)
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('链接无效')
    sensitive = {'token', 'access_token', 'refresh_token', 'api_key', 'apikey', 'secret', 'password', 'cookie', 'authorization', 'signature', 'sig',
                 'x-amz-signature', 'x-amz-credential', 'x-amz-security-token', 'x-goog-signature', 'x-goog-credential'}
    parameters = parse_qsl(parts.query, keep_blank_values=True) + parse_qsl(parts.fragment, keep_blank_values=True)
    if any(key.lower() in sensitive for key, _ in parameters):
        raise ValueError('链接不能包含凭据参数')
    return value


def _isbn(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{13}', value):
        raise ValueError('ISBN-13无效')
    if (sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value[:12])) + int(value[-1])) % 10:
        raise ValueError('ISBN-13校验位无效')
    return value


def _items(payload, name, allowed):
    rows = payload.get(name, [])
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError(f'{name}必须是最多1000条的数组')
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValueError(f'{name}[{index}]包含不支持的字段')
        yield row


def _account_category_paths(item):
    if 'categories' in item:
        values = item['categories']
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError('categories必须是最多50项的数组')
    elif item.get('category') is not None:
        values = [item['category']]
    else:
        return None

    paths = []
    seen = set()
    for value in values:
        text = _text(value, '赛道', 512, required=True)
        parts = [_text(part, '赛道层级', 128, required=True)
                 for part in re.split(r'\s*[/／]\s*', text)]
        if len(parts) > 8:
            raise ValueError('赛道最多支持8级')
        if len(' / '.join(parts)) > 512:
            raise ValueError('赛道路径过长')
        path = tuple(parts)
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _account_category_paths_in_db(session, account_id):
    return {row[0] for row in (session.query(TrackCategory.path)
                               .join(AccountCategory, AccountCategory.category_id == TrackCategory.id)
                               .filter(AccountCategory.account_id == account_id).all())}


def _sync_account_categories(session, account, paths):
    desired_ids = set()
    for parts in paths:
        parent = None
        current = []
        leaf = None
        for name in parts:
            current.append(name)
            path = ' / '.join(current)
            category = session.query(TrackCategory).filter_by(path=path).first()
            if category is None:
                category = TrackCategory(parent_id=parent.id if parent else None, name=name, path=path)
                session.add(category)
                session.flush()
            elif category.parent_id != (parent.id if parent else None):
                raise ImportConflict('赛道路径父级冲突')
            parent = category
            leaf = category
        if leaf is not None:
            desired_ids.add(leaf.id)

    existing_ids = {row[0] for row in session.query(AccountCategory.category_id)
                    .filter(AccountCategory.account_id == account.id).all()}
    if existing_ids == desired_ids:
        return False
    if existing_ids:
        session.query(AccountCategory).filter(AccountCategory.account_id == account.id).delete(synchronize_session=False)
    session.add_all(AccountCategory(account_id=account.id, category_id=category_id) for category_id in desired_ids)
    session.flush()
    return True


@contextmanager
def _row_context(section, index):
    try:
        yield
    except ImportConflict as exc:
        raise ImportConflict(f'{section}[{index}]: {exc}') from None
    except ValueError as exc:
        raise ValueError(f'{section}[{index}]: {exc}') from None


def _resolve(session, model, source, key, field):
    key = _text(key, field, required=True)
    row = session.query(model).filter_by(source=source, source_key=key).first()
    if row is None:
        raise ValueError(f'{field}引用不存在: {key}')
    return row


def _identity(session, model, source, item, fields, required, relations=None):
    key = _text(item.get('source_key'), 'source_key', required=True)
    timestamp = _observed_time(item.get('observed_at'))
    relation_values = relations or {}
    row = session.query(model).filter_by(source=source, source_key=key).first()
    normalized = {}
    for field, limit in fields.items():
        if field in item:
            value = item[field]
            if field == 'is_ai_content':
                if value is not None and not isinstance(value, bool):
                    raise ValueError('is_ai_content必须是布尔值')
            elif field == 'isbn13':
                value = _isbn(value)
            elif field in ('published_at', 'transcript_updated_at'):
                value = _time(value) if value is not None else None
            elif field == 'source_url':
                value = _url(value)
            else:
                value = _text(value, field, limit=limit)
            if value is not None:
                normalized[field] = value
    for field in required:
        if field not in normalized and row is None:
            raise ValueError(f'{field}不能为空')
    if row is None:
        params = {'source': source, 'source_key': key, 'last_observed_at': timestamp, **normalized, **relation_values}
        if model in (Account, Listing, Video):
            params['platform'] = 'wechat_channels'
        row = model(**params)
        session.add(row)
        session.flush()
        return row, True
    for field in ('external_id', 'isbn13'):
        if field in normalized:
            old = getattr(row, field)
            if old and old != normalized[field]:
                raise ImportConflict(f'{field}身份冲突')
    for field, value in relation_values.items():
        old = getattr(row, field)
        if old is not None and old != value:
            raise ImportConflict(f'{field}关联冲突')
    changed = False
    for field in ('external_id', 'isbn13'):
        if field in normalized and getattr(row, field) is None:
            setattr(row, field, normalized[field])
            changed = True
    for field, value in relation_values.items():
        if getattr(row, field) is None:
            setattr(row, field, value)
            changed = True
    if row.last_observed_at == timestamp:
        for field, value in normalized.items():
            previous = getattr(row, field)
            if previous is not None and previous != value:
                raise ImportConflict('同一时刻主数据内容冲突')
            if previous is None:
                setattr(row, field, value)
                changed = True
    if row.last_observed_at is None or timestamp > row.last_observed_at:
        for field, value in normalized.items():
            setattr(row, field, value)
        row.last_observed_at = timestamp
        changed = True
    return row, changed


def _decimal(value, field, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', str(value)):
        raise ValueError(f'{field}无效')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f'{field}无效') from None
    if number > Decimal('9999999999.99') or (maximum is not None and number > maximum):
        raise ValueError(f'{field}超出范围')
    return format(number.quantize(Decimal('0.01')), 'f')


def _values(kind, values, quality):
    fields = {
        'account': {'total_posts', 'showcase_rating', 'buyers_count', 'total_sales', 'repeat_customers', 'total_products'},
        'showcase': {'cumulative_sales', 'price', 'currency', 'availability'},
        'video': {'likes', 'shares', 'comments', 'is_pinned'},
    }
    if not isinstance(values, dict) or not values or not isinstance(quality, dict) or set(values) != set(quality) or set(values) - fields[kind]:
        raise ValueError('观测指标无效')
    result = {}
    for key, value in values.items():
        q = quality[key]
        if q not in ('exact', 'rounded', 'lower_bound', 'unknown'):
            raise ValueError(f'{key}质量无效')
        if q == 'unknown':
            if value is not None:
                raise ValueError(f'{key}未知值必须为空')
            result[key] = None
        elif q != 'exact':
            result[key] = _text(value, key, 128, required=True)
        elif key in ('showcase_rating', 'price'):
            result[key] = _decimal(value, key, Decimal('5') if key == 'showcase_rating' else None)
        elif key == 'is_pinned':
            if not isinstance(value, bool):
                raise ValueError('is_pinned必须是布尔值')
            result[key] = value
        elif key in ('currency', 'availability'):
            if key == 'currency' and value != 'CNY':
                raise ValueError('仅支持CNY')
            result[key] = _text(value, key, 32, required=True)
        else:
            if type(value) is int:
                value = str(value)
            if not isinstance(value, str) or not re.fullmatch(r'0|[1-9]\d*', value) or int(value) > 9223372036854775807:
                raise ValueError(f'{key}必须是非负整数')
            result[key] = value
    return result


def import_batch(session, payload, *, actor_id, idempotency_key, expected_revision=None):
    if not isinstance(payload, dict):
        raise ValueError('导入内容必须是对象')
    source = _text(payload.get('source'), '来源', 100, required=True)
    version = _text(payload.get('adapter_version'), '版本', 64, required=True)
    key = _text(idempotency_key, '幂等标识', 128, required=True)
    if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 5 * 1024 * 1024:
        raise ValueError('文件超过5 MiB')
    digest = _digest(payload)
    batch = session.query(IngestBatch).filter_by(source=source, idempotency_key=key).with_for_update().first()
    if batch:
        if batch.payload_hash != digest:
            raise ImportConflict('同一幂等标识对应不同内容')
        if batch.status == 'committed':
            return batch.counts
        if batch.status == 'rejected':
            raise ImportConflict('该批次已被拒绝，请检查后使用新的幂等标识')
        if batch.status == 'accepted' and batch.attempted_at > datetime.utcnow() - timedelta(minutes=5):
            raise ImportConflict('该导入批次正在处理中')
        batch.status = 'accepted'
        batch.attempted_at = datetime.utcnow()
        batch.attempt_count += 1
        batch.error_summary = None
    else:
        batch = IngestBatch(source=source, adapter_version=version, idempotency_key=key,
                            payload_hash=digest, actor_user_id=actor_id, status='accepted')
        session.add(batch)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ImportConflict('该导入批次正在处理中，请稍后重试') from None
    batch_id = batch.id
    try:
        return _import_batch(session, payload, actor_id=actor_id, idempotency_key=key,
                             expected_revision=expected_revision, batch_id=batch_id)
    except Exception as exc:
        session.rollback()
        batch = session.get(IngestBatch, batch_id)
        if batch and batch.status == 'accepted':
            batch.status = 'rejected' if isinstance(exc, ValueError) else 'failed'
            batch.error_summary = {'code': 'conflict' if isinstance(exc, ImportConflict) else 'invalid_input' if isinstance(exc, ValueError) else 'internal_error'}
            if isinstance(exc, ValueError):
                batch.error_summary['message'] = str(exc)[:500]
            session.commit()
        raise


def preview_batch(session, payload, *, actor_id):
    try:
        state = session.get(State, 1)
        revision = state.input_revision if state else 0
        result = _import_batch(session, payload, actor_id=actor_id, idempotency_key='preview-' + str(uuid4()), commit=False)
        return {**result, 'input_revision': revision, 'payload_hash': _digest(payload)}
    finally:
        session.rollback()


def _import_batch(session, payload, *, actor_id, idempotency_key, expected_revision=None, commit=True, batch_id=None):
    allowed = {'source', 'adapter_version', 'accounts', 'books', 'listings', 'videos', 'account_listings', 'video_listing_links', 'observations'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('导入包含不支持的字段')
    source = _text(payload.get('source'), '来源', 100, required=True)
    version = _text(payload.get('adapter_version'), '版本', 64, required=True)
    key = _text(idempotency_key, '幂等标识', 128, required=True)
    if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 5 * 1024 * 1024:
        raise ValueError('文件超过5 MiB')
    payload_hash = _digest(payload)
    if batch_id is None and commit:
        raise RuntimeError('missing accepted batch')
    state = session.query(State).filter_by(id=1).with_for_update().first()
    if state is None:
        state = State(id=1, input_revision=0, active_rules_version=1, rules_config={})
        session.add(state)
        session.flush()
    if expected_revision is not None and state.input_revision != expected_revision:
        raise ImportConflict('数据已变化，请重新预览')
    if batch_id is None:
        batch = IngestBatch(source=source, adapter_version=version, idempotency_key=key, payload_hash=payload_hash,
                            actor_user_id=actor_id, status='accepted')
        session.add(batch)
        session.flush()
    else:
        batch = session.get(IngestBatch, batch_id)
        if batch is None or batch.status != 'accepted':
            raise ImportConflict('导入批次状态已变化')
    changed = False
    result = {'created_observations': 0, 'duplicate_observations': 0}
    specs = [
        ('accounts', Account, {'name': 255, 'external_id': 255, 'category': 128, 'bio': 10000, 'location': 128, 'is_ai_content': 1, 'source_url': 2048}, ['name']),
        ('books', Book, {'title': 500, 'isbn13': 13, 'author': 255, 'publisher': 255, 'edition': 128, 'category': 128}, ['title']),
        ('listings', Listing, {'title': 500, 'external_id': 255, 'shop_external_id': 255, 'shop_name': 255, 'variant': 255, 'source_url': 2048}, ['title']),
        ('videos', Video, {'title': 10000, 'external_id': 255, 'published_at': 64, 'source_url': 2048, 'core_hook': 10000, 'script_text': 100000, 'transcript_status': 32, 'transcript_source': 128, 'transcript_updated_at': 64}, ['title']),
    ]
    for name, model, fields, required in specs:
        relation = {'book'} if name == 'listings' else {'account'} if name == 'videos' else set()
        extra_fields = {'categories'} if name == 'accounts' else set()
        for index, item in enumerate(_items(payload, name, set(fields) | relation | extra_fields | {'source_key', 'observed_at'})):
            with _row_context(name, index):
                relations = {}
                if name == 'listings' and item.get('book') is not None:
                    relations['book_id'] = _resolve(session, Book, source, item['book'], 'book').id
                if name == 'videos':
                    relations['account_id'] = _resolve(session, Account, source, item.get('account'), 'account').id
                category_paths = _account_category_paths(item) if name == 'accounts' else None
                account_key = _text(item.get('source_key'), 'source_key', required=True) if name == 'accounts' else None
                previous_account = (session.query(Account).filter_by(source=source, source_key=account_key).first()
                                   if name == 'accounts' else None)
                previous_account_timestamp = previous_account.last_observed_at if previous_account else None
                item_timestamp = _observed_time(item.get('observed_at')) if name == 'accounts' else None
                identity, modified = _identity(session, model, source, item, fields, required, relations)
                if name == 'accounts' and category_paths is not None:
                    if previous_account_timestamp is None or item_timestamp >= previous_account_timestamp:
                        current_paths = _account_category_paths_in_db(session, identity.id)
                        desired_paths = {' / '.join(parts) for parts in category_paths}
                        if (previous_account_timestamp == item_timestamp and current_paths
                                and current_paths != desired_paths):
                            raise ImportConflict('同一时刻账号赛道关系冲突')
                        modified |= _sync_account_categories(session, identity, category_paths)
                changed |= modified
    for index, item in enumerate(_items(payload, 'account_listings', {'account', 'listing', 'observed_at', 'status'})):
        with _row_context('account_listings', index):
            account = _resolve(session, Account, source, item.get('account'), 'account')
            listing = _resolve(session, Listing, source, item.get('listing'), 'listing')
            timestamp = _observed_time(item.get('observed_at'))
            status = item.get('status', 'unknown')
            if status not in ('active', 'inactive', 'unknown'):
                raise ValueError('橱窗状态无效')
            row = session.query(AccountListing).filter_by(account_id=account.id, listing_id=listing.id).first()
            if row is None:
                session.add(AccountListing(account_id=account.id, listing_id=listing.id, first_seen_at=timestamp, last_seen_at=timestamp, status=status))
                changed = True
            elif timestamp > row.last_seen_at:
                row.last_seen_at, row.status = timestamp, status
                changed = True
            elif timestamp < row.first_seen_at:
                row.first_seen_at = timestamp
                changed = True
            session.flush()
    for index, item in enumerate(_items(payload, 'video_listing_links', {'video', 'account', 'listing', 'valid_from', 'valid_to', 'observed_at', 'relation_type', 'verification_status', 'evidence_reference'})):
        with _row_context('video_listing_links', index):
            video = _resolve(session, Video, source, item.get('video'), 'video')
            account = _resolve(session, Account, source, item.get('account'), 'account')
            listing = _resolve(session, Listing, source, item.get('listing'), 'listing')
            link_target = session.query(AccountListing).filter_by(account_id=account.id, listing_id=listing.id).first()
            if video.account_id != account.id or link_target is None:
                raise ValueError('视频和橱窗必须属于同一账号')
            start, observed = _time(item.get('valid_from')), _observed_time(item.get('observed_at'))
            end = _time(item['valid_to']) if item.get('valid_to') else None
            relation, verification = item.get('relation_type'), item.get('verification_status')
            if start > observed or (end is not None and (end <= start or end > observed)) or relation not in ('attached', 'mentioned') or verification not in ('confirmed', 'unverified', 'rejected'):
                raise ValueError('挂载关系时间或状态无效')
            evidence = _url(item.get('evidence_reference'))
            links = session.query(VideoListingLink).filter_by(video_id=video.id, account_listing_id=link_target.id).all()
            same = next((x for x in links if x.valid_from == start), None)
            if same:
                if same.relation_type != relation or same.verification_status != verification or (same.valid_to is not None and same.valid_to != end) or (same.evidence_reference and evidence and same.evidence_reference != evidence):
                    raise ImportConflict('挂载关系冲突')
                if same.valid_to is None and end is not None:
                    same.valid_to = end
                    changed = True
            else:
                if any((x.valid_to is None or x.valid_to > start) and (end is None or x.valid_from < end) for x in links):
                    raise ImportConflict('挂载关系时间重叠')
                session.add(VideoListingLink(video_id=video.id, account_listing_id=link_target.id, valid_from=start, valid_to=end, observed_at=observed, relation_type=relation, verification_status=verification, evidence_reference=evidence))
                changed = True
            session.flush()
    for index, item in enumerate(_items(payload, 'observations', {'kind', 'account', 'listing', 'video', 'scope', 'observed_at', 'values', 'quality'})):
        with _row_context('observations', index):
            kind = item.get('kind')
            if kind not in ('account', 'showcase', 'video'):
                raise ValueError('观测类型无效')
            account = _resolve(session, Account, source, item.get('account'), 'account')
            timestamp = _observed_time(item.get('observed_at'))
            quality = item.get('quality')
            canonical = _values(kind, item.get('values'), quality)
            scope, account_listing, listing, video = None, None, None, None
            if kind == 'showcase':
                listing = _resolve(session, Listing, source, item.get('listing'), 'listing')
                account_listing = session.query(AccountListing).filter_by(account_id=account.id, listing_id=listing.id).first()
                if account_listing is None:
                    raise ValueError('橱窗商品引用不存在')
                scope = item.get('scope', 'unknown')
                if scope not in ('account_listing', 'platform_listing', 'unknown'):
                    raise ValueError('销量范围无效')
                target = ('listing:' + listing.id if scope == 'platform_listing' else ('account_listing:' if scope == 'account_listing' else 'unknown:') + account_listing.id)
            elif kind == 'video':
                video = _resolve(session, Video, source, item.get('video'), 'video')
                if video.account_id != account.id:
                    raise ValueError('视频账号不匹配')
                target = 'video:' + video.id
            else:
                target = 'account:' + account.id
            series = source + ':' + (scope or kind) + ':v1'
            slot = _digest([target, series, timestamp.isoformat()])
            content = _digest([slot, canonical, quality])
            existing = session.query(Observation).filter_by(slot_key=slot).first()
            if existing:
                if existing.content_hash != content:
                    raise ImportConflict('同一时刻的观测值冲突')
                result['duplicate_observations'] += 1
                continue
            business_date = timestamp.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date()
            session.add(Observation(batch_id=batch.id, kind=kind, account_id=account.id,
                                    account_listing_id=account_listing.id if account_listing else None,
                                    listing_id=listing.id if listing else None, video_id=video.id if video else None,
                                    metric_scope=scope, target_key=target, series_key=series, slot_key=slot,
                                    observed_at=timestamp, business_date=business_date, values=canonical,
                                    quality=quality, content_hash=content))
            session.flush()
            result['created_observations'] += 1
            changed = True
    if changed:
        state.input_revision += 1
    batch.status, batch.counts, batch.source_snapshot = 'committed', result, payload
    if commit:
        session.commit()
    else:
        session.flush()
    return result
