"""Validated writes and atomic mock replacement for the shared administrator catalog."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import Boolean, Integer, Numeric, DateTime, JSON, String
from models import db
from models.x_content import MODELS

REQUIRED = {'patterns':'name', 'cases':'raw_text', 'posts':'content', 'tactics':'tactic_name'}
ENUMS = {'category': {'cold_start_engagement','viral_reach','negative_avoid','emotional_hook','pain_point_tool','viral_story'},
         'evidence_status': {'supported','testing','avoid'}, 'status': {'draft_variant','published','archived'},
         'action_type': {'reply','ignore','block'}}
SERVER_FIELDS = {'created_at','updated_at','is_mock','source_batch','reply_rate','follower_leverage','like_rate'}

def model_for(kind):
    if kind not in MODELS: raise ValueError('未知栏目')
    return MODELS[kind]

def serialize(row):
    result = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        result[col.name] = value.isoformat() + 'Z' if isinstance(value, datetime) else float(value) if isinstance(value, Decimal) else value
    if hasattr(row, 'views'):
        result['reply_rate'] = round(row.replies / row.views * 100, 2) if row.views and row.replies is not None else None
        result['like_rate'] = round(row.likes / row.views * 100, 2) if row.views and row.likes is not None else None
    if hasattr(row, 'author_followers'):
        result['follower_leverage'] = round(row.views / row.author_followers, 2) if row.author_followers and row.views is not None else None
    return result

def validate(kind, data):
    model = model_for(kind)
    if not isinstance(data, dict): raise ValueError('记录必须是 JSON 对象')
    unknown = set(data) - set(model.__table__.columns.keys()) - SERVER_FIELDS
    if unknown: raise ValueError('未知字段：' + ', '.join(sorted(unknown)))
    clean = {}
    for key, value in data.items():
        if key in SERVER_FIELDS: continue
        col = model.__table__.columns[key]
        if value is None:
            if not col.nullable: raise ValueError(f'{key} 不能为空')
        elif isinstance(col.type, Boolean):
            if type(value) is not bool: raise ValueError(f'{key} 必须是布尔值')
        elif isinstance(col.type, Integer):
            if type(value) is not int or not 0 <= value <= 9223372036854775807: raise ValueError(f'{key} 必须是非负整数')
            if key == 'early_window_minutes' and not 1 <= value <= 1440: raise ValueError('冷启动窗口须为 1–1440 分钟')
        elif isinstance(col.type, Numeric):
            if type(value) not in (int, float) or not 0 <= value < 1000000: raise ValueError(f'{key} 必须是有效非负数字')
        elif isinstance(col.type, DateTime):
            try:
                value = datetime.fromisoformat(value.replace('Z','+00:00'))
                if value.tzinfo is None: raise ValueError()
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, TypeError, AttributeError): raise ValueError(f'{key} 必须是带时区的 ISO 时间')
        elif isinstance(col.type, JSON):
            if not isinstance(value, list) or len(value) > 1000 or any(not isinstance(v,str) or not v.strip() or len(v)>10000 for v in value): raise ValueError(f'{key} 必须是非空字符串组成的数组')
            value = list(dict.fromkeys(value))
        elif isinstance(col.type, String):
            if not isinstance(value,str) or len(value) > (col.type.length or 100000): raise ValueError(f'{key} 文本类型或长度错误')
            if key in (REQUIRED[kind], 'id') and not value.strip(): raise ValueError(f'{key} 不能为空')
        if key in ENUMS and value not in ENUMS[key]: raise ValueError(f'{key} 选项无效')
        if key == 'source_url' and value:
            from urllib.parse import urlparse
            url = urlparse(value)
            if url.scheme not in ('http','https') or not url.netloc: raise ValueError('原帖链接须为 http/https URL')
        clean[key] = value
    return clean

def _write(kind, data, mock=False, batch=None):
    clean = validate(kind, data); model = model_for(kind)
    ident = clean.get('id') or f'{kind[:3].upper()}-{uuid.uuid4().hex[:24]}'
    row = db.session.get(model, ident)
    if row is None:
        if not clean.get(REQUIRED[kind]): raise ValueError(f'缺少必填字段 {REQUIRED[kind]}')
        row = model(id=ident, is_mock=mock, source_batch=batch); db.session.add(row)
    elif mock:
        if not row.is_mock: raise ValueError('示例 ID 与真实数据冲突')
        return row
    elif batch:
        row.is_mock = False; row.source_batch = batch
    for key,value in clean.items(): setattr(row,key,value)
    if hasattr(row,'pattern_id') and row.pattern_id and db.session.get(MODELS['patterns'], row.pattern_id) is None:
        raise ValueError(f'关联套路不存在：{row.pattern_id}')
    if kind == 'posts':
        for ref in row.ref_case_ids or []:
            if db.session.get(MODELS['cases'], ref) is None: raise ValueError(f'关联案例不存在：{ref}')
    db.session.flush()
    return row

def save_record(kind, data):
    try:
        row = _write(kind,data); db.session.commit(); return row
    except Exception:
        db.session.rollback(); raise

def _clear_mock():
    # Refuse rather than silently break a real record's provenance.
    patterns = {r.id for r in MODELS['patterns'].query.filter_by(is_mock=True)}
    cases = {r.id for r in MODELS['cases'].query.filter_by(is_mock=True)}
    for kind in ('cases','posts'):
        for row in MODELS[kind].query.filter_by(is_mock=False):
            if row.pattern_id in patterns or (kind == 'posts' and cases.intersection(row.ref_case_ids or [])):
                raise ValueError('真实记录仍引用示例套路或案例，请先修改关联后再清理')
    count = 0
    for kind in ('posts','cases','tactics','patterns'):
        count += MODELS[kind].query.filter_by(is_mock=True).delete(synchronize_session=False)
    db.session.flush(); return count

def clear_mock():
    try:
        count = _clear_mock(); db.session.commit(); return {'deleted': count}
    except Exception:
        db.session.rollback(); raise

def import_bundle(bundle, replace_mock=False, mock=False):
    try:
        bundle = normalize_bundle(bundle)
        if not isinstance(bundle,dict) or not bundle or set(bundle)-set(MODELS): raise ValueError('文件需包含 patterns / cases / posts / tactics 数组')
        if any(not isinstance(v,list) for v in bundle.values()): raise ValueError('每个栏目必须是数组')
        if not 1 <= sum(map(len,bundle.values())) <= 5000: raise ValueError('每次需导入 1–5000 条记录')
        for kind, rows in bundle.items():
            seen = set()
            for row in rows:
                clean = validate(kind,row)
                if not clean.get('id'): raise ValueError('导入记录必须包含稳定的 id')
                if clean['id'] in seen: raise ValueError('文件包含重复 ID：'+clean['id'])
                seen.add(clean['id'])
        batch = ('mock-' if mock else 'import-') + uuid.uuid4().hex
        counts = {'new':0,'updated':0,'deleted_mock':0}
        # Upsert first so imported records can replace existing mock references.
        for kind in MODELS:
            for data in bundle.get(kind,[]):
                exists = db.session.get(MODELS[kind],data['id']) is not None
                _write(kind,data,mock,batch)
                counts['updated' if exists else 'new'] += 1
        if replace_mock: counts['deleted_mock'] = _clear_mock()
        db.session.commit(); return counts
    except Exception:
        db.session.rollback(); raise

def seed_mock():
    from services.x_content_mock import MOCK_BUNDLE
    return import_bundle(MOCK_BUNDLE, mock=True)


def normalize_bundle(bundle):
    """Adapt the supplied v1 export without losing references to unavailable cases."""
    if not isinstance(bundle, dict) or not any(k.startswith('x_') for k in bundle): return bundle
    tables = {m.__tablename__:kind for kind,m in MODELS.items()}
    if set(bundle) - set(tables) - {'meta'}: raise ValueError('未知导入表名')
    result = {}
    aliases = {'pattern_code':'pattern_id','case_code':'id','post_code':'id','tactic_code':'id',
        'key_takeaway':'evidence_notes','captured_at':'metrics_captured_at','screenshot_url':'screenshot_path',
        'ref_case_codes':'ref_case_ids','time_slot_label':'time_slot','early_burst_metrics':'early_burst_notes',
        'review_summary':'review_notes','reply_formula':'reply_logic','matched_handles':'frequent_friends'}
    for table,kind in tables.items():
        if table not in bundle: continue
        rows = bundle[table]
        if not isinstance(rows,list): raise ValueError('每个表必须是数组')
        result[kind] = []
        for original in rows:
            if not isinstance(original,dict): raise ValueError('记录必须是对象')
            row = {}
            for key,value in original.items():
                target = 'id' if key == 'pattern_code' and kind == 'patterns' else aliases.get(key,key)
                if target in row: raise ValueError('重复字段映射：'+target)
                if target in ('published_at','metrics_captured_at') and value:
                    try:
                        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
                        if parsed.tzinfo is None: value = parsed.isoformat() + '+08:00'
                    except (ValueError,TypeError,AttributeError): raise ValueError('日期格式无效')
                if target == 'is_negative_sample' and type(value) is int and value in (0,1): value = bool(value)
                if target == 'category' and value == 'cold_start': value = 'cold_start_engagement'
                if target == 'status' and value == 'ready_draft': value = 'draft_variant'
                row[target] = value
            if kind == 'tactics' and 'action_type' not in row and 'scenario_type' in row:
                row['action_type'] = 'block' if row['scenario_type'] == 'bot_defense' else 'reply'
            result[kind].append(row)
    known = {r['id'] for r in result.get('cases',[]) if 'id' in r}
    known.update(r.id for r in MODELS['cases'].query.filter_by(is_mock=False))
    for row in result.get('posts',[]):
        if 'ref_case_ids' not in row: continue
        refs = row.get('ref_case_ids',[])
        if not isinstance(refs,list) or any(not isinstance(r,str) for r in refs): raise ValueError('案例引用必须是字符串数组')
        row['unresolved_case_ids'] = list(dict.fromkeys(row.get('unresolved_case_ids',[]) + [r for r in refs if r not in known]))
        row['ref_case_ids'] = [r for r in refs if r in known]
    return result
