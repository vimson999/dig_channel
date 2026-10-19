"""Offline import of browser API captures; never stores request headers or calls upstream."""
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import text
from sqlalchemy.orm import load_only
from models import db
from models.zsxq_sync import ZsxqSyncRun, ZsxqTopic, ZsxqTopicComment, jsonb_payload_md5
from services.zsxq_sync_service import zsxq_sync_service

MAX_SOURCE_BYTES = 12 * 1024 * 1024
LABELS = {'all': '最新', 'digests': '精华', 'by_owner': '只看星主',
          'questions': '问答', 'with_files': '文件', 'with_images': '图片',
          'unanswered_questions': '等我回答'}


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ValueError('导出或采集时间无效')


def _merge(old, new):
    """Missing fields never erase a more complete snapshot."""
    result = deepcopy(old)
    for key, value in new.items():
        if value is None or value == '' or value == []:
            continue
        if key in ('show_comments', 'replied_comments', 'files', 'images') and isinstance(value, list) and isinstance(result.get(key), list):
            identity = {'show_comments': 'comment_id', 'replied_comments': 'comment_id', 'files': 'file_id', 'images': 'image_id'}[key]
            combined = {str(item[identity]): deepcopy(item) for item in result[key] if isinstance(item, dict) and identity in item}
            if len(combined) == len(result[key]) and all(isinstance(item, dict) and identity in item for item in value):
                for item in value:
                    item_id = str(item[identity])
                    combined[item_id] = _merge(combined.get(item_id, {}), item)
                result[key] = list(combined.values())
            else:
                result[key] = deepcopy(value)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def parse_capture(source):
    if not isinstance(source, dict) or str(source.get('schemaVersion')) != '1':
        raise ValueError('仅支持 schemaVersion=1 的 API 采集 JSON')
    group_id = str(source.get('groupId') or '')
    if not group_id.isdigit() or len(group_id) > 32:
        raise ValueError('groupId 无效')
    exported = _time(source.get('exportedAt'))
    records = source.get('records')
    if not isinstance(records, list) or not records or len(records) > 1000:
        raise ValueError('records 必须包含 1–1000 条采集记录')
    entries = {}; skipped = 0; group_name = None; pages = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('采集记录必须是对象')
        request = record.get('request') or {}; response = record.get('response') or {}
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise ValueError('请求或响应结构无效')
        url = urlsplit(str(request.get('url') or ''))
        match = re.fullmatch(r'/v2/groups/(\d+)/topics(/sticky)?', url.path)
        if url.scheme != 'https' or url.hostname != 'api.zsxq.com' or not match or match[1] != group_id:
            skipped += 1; continue
        if response.get('status') != 200 or response.get('bodyTruncated'):
            skipped += 1; continue
        body = response.get('body')
        if isinstance(body, str):
            try: body = json.loads(body)
            except ValueError:
                skipped += 1; continue
        if not isinstance(body, dict) or body.get('succeeded') is not True:
            skipped += 1; continue
        data = body.get('resp_data')
        topics = data.get('topics') if isinstance(data, dict) else None
        if not isinstance(topics, list):
            skipped += 1; continue
        query = parse_qs(url.query)
        scope = query.get('scope', ['all'])[0]
        if scope not in LABELS:
            skipped += 1; continue
        # Sticky is a placement in the main feed, not a separate content tab.
        if match[2]: scope = 'all'
        tab = {'id': 'preset:' + scope, 'label': LABELS[scope], 'scope': scope}
        hashtag = query.get('hashtag_id', [None])[0]
        if hashtag:
            if not hashtag.isdigit() or len(hashtag) > 64:
                raise ValueError('标签 ID 无效')
            tab = {'id': 'hashtag:' + hashtag, 'label': '标签 ' + hashtag, 'scope': scope}
        record_observed = _time(record.get('recordedAt') or source.get('exportedAt'))
        dates = []
        for topic in topics:
            observed = record_observed
            if not isinstance(topic, dict):
                raise ValueError('帖子必须是对象')
            group = topic.get('group') or {}
            if not isinstance(group, dict) or str(group.get('group_id')) != group_id:
                skipped += 1; continue
            topic_id = str(topic.get('topic_id') or '')
            if not topic_id.isdigit() or len(topic_id) > 64:
                raise ValueError('帖子 ID 无效')
            if not any(isinstance(topic.get(k), dict) for k in ('talk', 'question', 'article', 'answer')):
                # Sparse snapshots can update existing rows, but need an ID and timestamp.
                if not topic.get('create_time'):
                    skipped += 1; continue
            created = _time(topic.get('create_time'))
            dates.append(created.isoformat())
            group_name = str(group.get('name') or group_name or group_id)[:255]
            key = (tab['id'], topic_id)
            previous = entries.get(key)
            if previous:
                older, newer = (previous['payload'], topic) if observed >= previous['observed'] else (topic, previous['payload'])
                payload = _merge(older, newer)
                observed = max(observed, previous['observed'])
            else: payload = deepcopy(topic)
            # Comments inherited by a sparse topic merge were not observed in
            # this response. Preserve their original observation separately.
            comment_observations = list(previous['comment_observations']) if previous else []
            if topic.get('show_comments'):
                comment_observations.append({
                    'payload': {'topic_id': topic_id, 'show_comments': deepcopy(topic['show_comments'])},
                    'observed': record_observed,
                })
            entries[key] = {'tab': tab, 'payload': payload, 'observed': observed,
                            'comment_observations': comment_observations}
        pages.append({'tab_id': tab['id'], 'topics': len(topics),
                      'oldest': min(dates) if dates else None, 'newest': max(dates) if dates else None})
    if not entries:
        raise ValueError('没有可导入的目标星球帖子（请检查响应是否完整）')
    return {'group_id': group_id, 'group_name': group_name, 'exported_at': exported.isoformat(),
            'entries': list(entries.values()), 'skipped_responses_or_topics': skipped, 'pages': pages}


def _prepare(source):
    parsed = parse_capture(source)
    ids = {str(e['payload']['topic_id']) for e in parsed['entries']}
    # Keep each response below the local pooler's query timeout even when
    # large topic snapshots must travel over a slow database connection.
    existing = {}
    ordered_ids = sorted(ids)
    for offset in range(0, len(ordered_ids), 8):
        rows = ZsxqTopic.query.options(load_only(
            ZsxqTopic.id, ZsxqTopic.tab_id, ZsxqTopic.topic_id,
            ZsxqTopic.raw_payload, ZsxqTopic.last_sync_run_id, ZsxqTopic.last_synced_at,
        )).filter(ZsxqTopic.group_id == parsed['group_id'],
                  ZsxqTopic.topic_id.in_(ordered_ids[offset:offset + 8])).all()
        existing.update({(r.tab_id, r.topic_id): r for r in rows})
    report = {k: v for k, v in parsed.items() if k != 'entries'}
    report.update(unique_topics=len(ids), tab_records=len(parsed['entries']), new_records=0,
                  updated_records=0, unchanged_records=0, protected_records=0,
                  complete_history=False, warnings=[
                      '仅导入文件中捕获的帖子，时间跨度不代表历史完整。',
                      '评论可能不完整；附件和图片仅保存元数据，不自动下载。'])
    dates = [_time(e['payload']['create_time']) for e in parsed['entries']]
    report['oldest_topic_time'] = min(dates).isoformat(); report['latest_topic_time'] = max(dates).isoformat()
    for entry in parsed['entries']:
        payload = entry['payload']; row = existing.get((entry['tab']['id'], str(payload['topic_id'])))
        entry['row'] = row
        if row is None:
            action = 'new'
        else:
            old = row.raw_payload or {}
            old_stamp = old.get('_json_import_observed_at')
            previous_run = db.session.get(ZsxqSyncRun, row.last_sync_run_id) if row.last_sync_run_id else None
            if previous_run and previous_run.mode == 'json_import' and old_stamp:
                baseline = _time(old_stamp)
            else:
                baseline = row.last_synced_at.replace(tzinfo=timezone.utc) if row.last_synced_at else None
            if baseline and entry['observed'] < baseline:
                action = 'protected'
            elif old.get('modify_time') and payload.get('modify_time') and _time(payload['modify_time']) < _time(old['modify_time']):
                action = 'protected'
            else:
                payload = _merge(old, payload)
                def comparable(p): return {k: v for k, v in p.items() if k != '_json_import_observed_at'}
                action = 'unchanged' if comparable(payload) == comparable(old) else 'updated'
        entry['payload'] = payload; entry['action'] = action
        report[action + '_records'] += 1
    return parsed, report


def preview_capture(source):
    return _prepare(source)[1]


def _import_comments(group_id, tab, payload, observed):
    comments = zsxq_sync_service._topic_comments(payload)
    if not comments:
        return
    existing = {r.comment_id: r for r in ZsxqTopicComment.query.filter(
        ZsxqTopicComment.group_id == group_id,
        ZsxqTopicComment.topic_id == str(payload['topic_id']),
        ZsxqTopicComment.comment_id.in_([str(c['comment_id']) for c in comments])).all()}
    accepted = []
    for comment in comments:
        old = existing.get(str(comment['comment_id']))
        if old:
            old_payload = old.raw_payload or {}
            stamp = old_payload.get('_json_import_observed_at')
            baseline = _time(stamp) if stamp else old.last_synced_at.replace(tzinfo=timezone.utc)
            if observed < baseline:
                continue
            comment = _merge(old_payload, comment)
        # Replies have been flattened with explicit parent IDs; process once.
        comment.pop('replied_comments', None)
        comment['_json_import_observed_at'] = observed.isoformat()
        accepted.append(comment)
    zsxq_sync_service._upsert_topic_comments(group_id, tab,
        {'topic_id': payload['topic_id'], 'show_comments': accepted})


def import_capture(source):
    try:
        # Serialize concurrent uploads for this group. The unique constraint also
        # protects against races with the independent upstream sync worker.
        if db.engine.dialect.name == 'postgresql':
            group_id = parse_capture(source)['group_id']
            lock_key = int.from_bytes(hashlib.sha256(('json-import:' + group_id).encode()).digest()[:8], 'big', signed=True)
            db.session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_key})
        parsed, report = _prepare(source)
        now = datetime.utcnow()
        digest = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        run = ZsxqSyncRun(group_id=parsed['group_id'], group_name=parsed['group_name'], mode='json_import',
                          status='running', requested_tabs=list({e['tab']['id']: e['tab'] for e in parsed['entries']}.values()))
        db.session.add(run); db.session.flush()
        for entry in parsed['entries']:
            if entry['action'] == 'protected': continue
            payload = deepcopy(entry['payload']); payload['_json_import_observed_at'] = entry['observed'].isoformat()
            if entry['action'] == 'unchanged':
                row = entry['row']
                row.raw_payload = payload; row.raw_payload_hash = jsonb_payload_md5(payload)
                row.last_sync_run_id = run.id; row.last_synced_at = now
                for snapshot in entry['comment_observations']:
                    _import_comments(parsed['group_id'], entry['tab'], snapshot['payload'], snapshot['observed'])
                continue
            meta = zsxq_sync_service._topic_meta(payload)
            row = entry['row']
            if row is None:
                row = ZsxqTopic(group_id=parsed['group_id'], tab_id=entry['tab']['id'],
                                topic_id=str(payload['topic_id']), first_sync_run_id=run.id)
                db.session.add(row)
            row.group_name = parsed['group_name']; row.tab_label = entry['tab']['label']
            row.topic_uid = str(payload.get('topic_uid') or '')
            for key in ('title', 'author_name', 'content', 'create_time'): setattr(row, key, meta[key])
            row.raw_payload = payload; row.raw_payload_hash = jsonb_payload_md5(payload)
            # Old disk snapshot would hide the newly imported DB payload in existing readers.
            row.raw_json_path = None
            row.last_sync_run_id = run.id; row.last_synced_at = now; row.sync_projection_fields()
            for snapshot in entry['comment_observations']:
                _import_comments(parsed['group_id'], entry['tab'], snapshot['payload'], snapshot['observed'])
            zsxq_sync_service._upsert_attachments(parsed['group_id'], entry['tab'], payload)
        report['source_sha256'] = digest; report['imported_at'] = now.isoformat() + 'Z'
        report['run_id'] = run.id
        # Model hooks rebuild projection columns from these canonical keys.
        report.update(topics_fetched=report['tab_records'], topics_new=report['new_records'],
                      topics_updated=report['updated_records'],
                      topics_existing=report['unchanged_records'] + report['protected_records'])
        run.result = dict(report); run.status = 'completed'; run.completed_at = now
        run.topics_fetched = report['tab_records']; run.topics_new = report['new_records']
        run.topics_updated = report['updated_records']; run.topics_existing = report['unchanged_records'] + report['protected_records']
        db.session.commit()
        return report
    except Exception:
        db.session.rollback()
        raise
