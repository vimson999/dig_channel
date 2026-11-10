"""Validate and persist Shengcai project catalog snapshots without detail fetching."""
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import PurePath
from sqlalchemy import text
from models import db
from models.shengcai_project import ShengcaiProject, ShengcaiProjectImport

MAX_SOURCE_BYTES = 5 * 1024 * 1024
TAG_FIELDS = {'platforms': 'platformMenus', 'monetizations': 'monetizeMenus', 'crowds': 'crowdMenus'}
SORTS = {'newest', 'likes', 'cost', 'time', 'income_max', 'income_min'}
NUMERIC = ('incomeMin', 'incomeMax', 'dailyMinutes', 'estimatedCost', 'likeCount', 'gmtPublish', 'caseCount', 'resourceCount')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_catalog(source):
    if not isinstance(source, dict) or source.get('success') is not True:
        raise ValueError('需要 success=true 的生财项目列表 JSON')
    data = source.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or not data['items']:
        raise ValueError('data.items 必须是非空项目数组')
    items = data['items']; total = data.get('total', len(items))
    if isinstance(total, bool) or not isinstance(total, int) or total < len(items):
        raise ValueError('data.total 必须是不小于当前列表数量的整数')
    if len(items) > 5000:
        raise ValueError('单次最多导入 5000 个项目')
    ids = set()
    for item in items:
        if not isinstance(item, dict): raise ValueError('每个项目必须是对象')
        identity = item.get('id')
        if isinstance(identity, bool) or not isinstance(identity, (str, int)) or not str(identity).isdigit() or len(str(identity)) > 32 or int(identity) <= 0:
            raise ValueError('项目 id 必须是有效的正整数')
        key = str(int(identity))
        if key in ids: raise ValueError(f'项目 id 重复：{key}')
        ids.add(key)
        if not isinstance(item.get('name'), str) or not item['name'].strip() or len(item['name']) > 500:
            raise ValueError(f'项目 {key} 的名称无效')
        for field in ('summary', 'highlightText', 'coverImage', 'miniCoverImage'):
            if field in item and item[field] is not None and not isinstance(item[field], str):
                raise ValueError(f'项目 {key} 的 {field} 必须是文本')
        for field in NUMERIC:
            value = item.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(f'项目 {key} 的 {field} 必须是非负数')
        if item.get('incomeMin') is not None and item.get('incomeMax') is not None and item['incomeMin'] > item['incomeMax']:
            raise ValueError(f'项目 {key} 的收益区间倒置')
        for field in TAG_FIELDS.values():
            tags = item.get(field, [])
            if not isinstance(tags, list) or any(not isinstance(t, dict) or not isinstance(t.get('name'), str) or not t['name'].strip() for t in tags):
                raise ValueError(f'项目 {key} 的 {field} 标签结构无效')
    return items, total


def import_catalog(source, filename='catalog.json'):
    items, total = validate_catalog(source)
    source_hash = _digest(source)
    now = datetime.utcnow()
    try:
        if db.engine.dialect.name == 'postgresql':
            db.session.execute(text('SELECT pg_advisory_xact_lock(739402153)'))
        rows = {r.id: r for r in ShengcaiProject.query.all()}
        counts = {'new': 0, 'updated': 0, 'unchanged': 0}
        for item in items:
            key = str(int(item['id'])); digest = _digest(item); row = rows.get(key)
            if row is None:
                row = ShengcaiProject(id=key, first_imported_at=now)
                db.session.add(row); counts['new'] += 1
            elif row.payload_sha256 == digest:
                row.last_seen_at = now; counts['unchanged'] += 1; continue
            else: counts['updated'] += 1
            row.name = item['name']; row.summary = item.get('summary')
            row.raw_payload = item; row.payload_sha256 = digest
            row.last_seen_at = now; row.updated_at = now
        run = ShengcaiProjectImport(source_sha256=source_hash,
            source_filename=PurePath(str(filename)).name[:255] or 'catalog.json',
            item_count=len(items), source_total=total, new_count=counts['new'],
            updated_count=counts['updated'], unchanged_count=counts['unchanged'], imported_at=now)
        db.session.add(run); db.session.flush(); run_id = run.id
        db.session.commit()
        return {'run_id': run_id, 'items': len(items), 'source_total': total, **counts,
                'imported_at': now.isoformat() + 'Z', 'source_sha256': source_hash}
    except Exception:
        db.session.rollback()
        raise


def list_catalog(q='', platform='', monetize='', crowd='', sort='newest', page=1, per_page=24, secondary_sort='', published_within='all', name=''):
    if sort not in SORTS or secondary_sort not in SORTS | {''} or page < 1 or not 1 <= per_page <= 100:
        raise ValueError('排序或分页参数无效')
    if published_within not in {'all', '7', '30', '90', '365'}:
        raise ValueError('发布时间范围无效')
    now = datetime.now(timezone.utc).timestamp()
    since = now - int(published_within) * 86400 if published_within != 'all' else None
    # This catalog currently has hundreds of small list cards; each snapshot is
    # read once, then faceted in memory to keep SQLite/Postgres behavior equal.
    items = [r.raw_payload for r in ShengcaiProject.query.all()]
    facets = {key: [{'name': name, 'count': count} for name, count in
               Counter(t['name'] for item in items for t in item.get(field, [])).most_common()]
              for key, field in TAG_FIELDS.items()}
    all_count = len(items)
    def names(item, field): return {t['name'] for t in item.get(field, [])}
    query = q.strip().casefold()
    name_query = name.strip().casefold()
    filtered = [item for item in items if
                (not query or query in ' '.join(str(item.get(k) or '') for k in ('name', 'summary', 'highlightText')).casefold())
                and (not name_query or name_query in str(item.get('name') or '').casefold())
                and (since is None or (item.get('gmtPublish') is not None and since <= item['gmtPublish'] <= now))
                and (not platform or platform in names(item, 'platformMenus'))
                and (not monetize or monetize in names(item, 'monetizeMenus'))
                and (not crowd or crowd in names(item, 'crowdMenus'))]
    fields = {'newest': 'gmtPublish', 'likes': 'likeCount', 'cost': 'estimatedCost', 'time': 'dailyMinutes',
              'income_max': 'incomeMax', 'income_min': 'incomeMin'}
    descending = {'newest', 'likes', 'income_max', 'income_min'}
    # Stable sorts apply the less significant key first, before pagination.
    filtered.sort(key=lambda i: int(i['id']), reverse=sort in descending)
    rules = [sort] + ([secondary_sort] if secondary_sort and secondary_sort != sort else [])
    for rule in reversed(rules):
        field = fields[rule]
        reverse = rule in descending
        filtered.sort(key=lambda i: (i.get(field) is not None, i.get(field) or 0) if reverse
                      else (i.get(field) is None, i.get(field) or 0), reverse=reverse)
    last = ShengcaiProjectImport.query.order_by(ShengcaiProjectImport.imported_at.desc()).first()
    start = (page - 1) * per_page
    return {'items': filtered[start:start+per_page], 'total': len(filtered), 'catalog_total': all_count,
            'page': page, 'per_page': per_page, 'has_next': start + per_page < len(filtered),
            'facets': facets, 'last_imported_at': last.imported_at.isoformat() + 'Z' if last else None,
            'stats': {'platforms': len(facets['platforms']), 'monetizations': len(facets['monetizations']),
                      'case_references': sum(i.get('caseCount') or 0 for i in items),
                      'resource_references': sum(i.get('resourceCount') or 0 for i in items)}}
