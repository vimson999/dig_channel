import copy
import json
import sys
import unittest
from pathlib import Path
from flask import Flask
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/backend'))
from models import db
from models.zsxq_sync import ZsxqTopic, ZsxqSyncRun
from services.zsxq_json_import_service import parse_capture, preview_capture, import_capture


def capture(text='正文', exported='2026-10-19T03:12:00Z'):
    topic = {'topic_id': 123, 'group': {'group_id': 42, 'name': '测试星球'},
             'create_time': '2026-10-19T08:00:00+0800', 'type': 'talk',
             'talk': {'text': text, 'owner': {'name': '作者'}}, 'likes_count': 3}
    return {'schemaVersion': 1, 'groupId': '42', 'exportedAt': exported, 'records': [{
        'request': {'url': 'https://api.zsxq.com/v2/groups/42/topics?scope=all', 'headers': {'Cookie': 'secret'}},
        'recordedAt': exported, 'response': {'status': 200, 'bodyTruncated': False,
        'body': {'succeeded': True, 'resp_data': {'topics': [topic]}}}}]}


class JsonImportTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()
    def test_preview_does_not_write_and_import_is_idempotent(self):
        x = capture()
        self.assertEqual(preview_capture(x)['new_records'], 1)
        self.assertEqual(ZsxqTopic.query.count(), 0)
        self.assertEqual(import_capture(x)['new_records'], 1)
        result = import_capture(x)
        self.assertEqual(result['new_records'], 0)
        self.assertEqual(result['unchanged_records'], 1)
        self.assertEqual(ZsxqTopic.query.count(), 1)
        self.assertNotIn('secret', json.dumps(ZsxqSyncRun.query.first().result))
    def test_rejects_truncated_response_and_other_group(self):
        x = capture(); x['records'][0]['response']['bodyTruncated'] = True
        with self.assertRaises(ValueError): parse_capture(x)
        x = capture(); x['records'][0]['response']['body']['resp_data']['topics'][0]['group']['group_id'] = 9
        with self.assertRaises(ValueError): parse_capture(x)
    def test_filters_unrelated_endpoints(self):
        x = capture(); other = copy.deepcopy(x['records'][0]); other['request']['url'] = 'https://api.zsxq.com/v2/dynamics'
        other['response']['body']['resp_data']['topics'][0]['topic_id'] = 999
        x['records'].append(other)
        self.assertEqual(preview_capture(x)['unique_topics'], 1)
    def test_older_capture_cannot_overwrite_newer(self):
        import_capture(capture())
        r = import_capture(capture('旧正文', '2026-10-18T03:00:00Z'))
        self.assertEqual(r['protected_records'], 1)
        self.assertEqual(ZsxqTopic.query.first().content, '正文')
    def test_sparse_payload_preserves_missing_fields(self):
        import_capture(capture())
        x = capture(exported='2026-10-20T03:00:00Z')
        del x['records'][0]['response']['body']['resp_data']['topics'][0]['talk']
        x['records'][0]['response']['body']['resp_data']['topics'][0]['likes_count'] = 5
        import_capture(x)
        row = ZsxqTopic.query.first()
        self.assertEqual(row.content, '正文'); self.assertEqual(row.raw_payload['likes_count'], 5)
    def test_shared_comments_preserve_newer_and_missing_fields_across_tabs(self):
        from models.zsxq_sync import ZsxqTopicComment
        x = capture()
        x['records'][0]['response']['body']['resp_data']['topics'][0]['show_comments'] = [
            {'comment_id': 7, 'text': '新评论', 'owner': {'name': '评论作者'}, 'likes_count': 10}]
        import_capture(x)
        older = copy.deepcopy(x); older['exportedAt'] = '2026-10-18T03:00:00Z'
        older['records'][0]['recordedAt'] = older['exportedAt']
        older['records'][0]['request']['url'] = 'https://api.zsxq.com/v2/groups/42/topics?scope=digests'
        older['records'][0]['response']['body']['resp_data']['topics'][0]['show_comments'][0]['text'] = '旧评论'
        import_capture(older)
        self.assertEqual(ZsxqTopicComment.query.one().text, '新评论')
        newer = copy.deepcopy(older); newer['records'][0]['recordedAt'] = '2026-10-20T03:00:00Z'
        newer['records'][0]['response']['body']['resp_data']['topics'][0]['show_comments'] = [{'comment_id': 7, 'text': '更新'}]
        import_capture(newer)
        row = ZsxqTopicComment.query.one()
        self.assertEqual(row.text, '更新'); self.assertEqual(row.owner_name, '评论作者'); self.assertEqual(row.likes_count, 10)

    def test_http_preview_commit_and_history_roundtrip(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import patch
        from apis.v1.zsxq_json_import import bp
        self.app.register_blueprint(bp)
        client = self.app.test_client(); raw = json.dumps(capture()).encode()
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(is_admin=True)):
            preview = client.post('/api/v1/zsxq/json-import/preview', data={'file': (io.BytesIO(raw), 'sample.json')})
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(ZsxqTopic.query.count(), 0)
            digest = preview.json['data']['file_sha256']
            for index in range(2):
                response = client.post('/api/v1/zsxq/json-import/commit', data={
                    'file': (io.BytesIO(raw), 'sample.json'), 'file_sha256': digest})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['data']['new_records'], 1 if index == 0 else 0)
            history = client.get('/api/v1/zsxq/json-import/history')
            self.assertEqual(history.status_code, 200); self.assertEqual(len(history.json['data']), 2)
            self.assertNotIn('secret', json.dumps(history.json))

    def test_newer_identical_snapshot_advances_old_snapshot_protection(self):
        import_capture(capture())
        import_capture(capture(exported='2026-10-21T03:00:00Z'))
        r = import_capture(capture('旧内容', exported='2026-10-20T03:00:00Z'))
        self.assertEqual(r['protected_records'], 1)
        self.assertEqual(ZsxqTopic.query.one().content, '正文')

    def test_existing_snapshots_are_read_in_bounded_pages(self):
        from sqlalchemy import event
        x = capture(); topic = x['records'][0]['response']['body']['resp_data']['topics'][0]
        x['records'][0]['response']['body']['resp_data']['topics'] = [dict(copy.deepcopy(topic), topic_id=i) for i in range(1, 31)]
        import_capture(x)
        parameter_counts = []
        def observe(conn, cursor, statement, parameters, context, executemany):
            if 'zsxq_topics.topic_id IN' in statement:
                parameter_counts.append(len(parameters))
        event.listen(db.engine, 'before_cursor_execute', observe)
        try:
            result = preview_capture(x)
        finally:
            event.remove(db.engine, 'before_cursor_execute', observe)
        self.assertEqual(result['unchanged_records'], 30)
        self.assertTrue(parameter_counts)
        self.assertLessEqual(max(parameter_counts), 9)

    def test_import_run_projection_counters_survive_reload(self):
        first = import_capture(capture())
        db.session.remove()
        run = db.session.get(ZsxqSyncRun, first['run_id'])
        self.assertEqual(run.topics_fetched, 1)
        self.assertEqual(run.topics_new, 1)
        second = import_capture(capture('更新正文', exported='2026-10-20T03:00:00Z'))
        db.session.remove()
        run = db.session.get(ZsxqSyncRun, second['run_id'])
        self.assertEqual(run.topics_updated, 1)

    def test_sparse_newer_topic_does_not_republish_inherited_comments(self):
        from models.zsxq_sync import ZsxqTopicComment
        older = capture(exported='2026-10-18T03:00:00Z')
        older['records'][0]['response']['body']['resp_data']['topics'][0]['show_comments'] = [{'comment_id': 7, 'text': '旧评论'}]
        import_capture(older)
        newer = copy.deepcopy(older)
        newer['records'][0]['recordedAt'] = '2026-10-19T03:00:00Z'
        newer['records'][0]['request']['url'] = 'https://api.zsxq.com/v2/groups/42/topics?scope=digests'
        newer['records'][0]['response']['body']['resp_data']['topics'][0]['show_comments'][0]['text'] = '新评论'
        import_capture(newer)
        sparse = capture(exported='2026-10-20T03:00:00Z')
        import_capture(sparse)
        self.assertEqual(ZsxqTopicComment.query.one().text, '新评论')
        # Also keep per-response comment times when captures merge multiple records.
        mixed = copy.deepcopy(older)
        mixed['records'][0]['recordedAt'] = '2026-10-18T04:00:00Z'
        mixed['records'].extend(sparse['records'])
        import_capture(mixed)
        self.assertEqual(ZsxqTopicComment.query.one().text, '新评论')

    def test_invalid_capture(self):
        for x in ({}, {'groupId': '42', 'records': []}, {'groupId': '42', 'records': 'bad'}):
            with self.assertRaises(ValueError): parse_capture(x)
    def test_tabs_are_independent(self):
        x = capture(); other = copy.deepcopy(x['records'][0]); other['request']['url'] += '&scope=digests'
        # Use an unambiguous scope, matching actual capture URLs.
        other['request']['url'] = 'https://api.zsxq.com/v2/groups/42/topics?scope=digests'
        x['records'].append(other)
        r = import_capture(x)
        self.assertEqual(r['unique_topics'], 1); self.assertEqual(r['new_records'], 2)
    def test_transaction_rolls_back_on_projection_error(self):
        from unittest.mock import patch
        with patch('services.zsxq_json_import_service.zsxq_sync_service._upsert_attachments', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): import_capture(capture())
        self.assertEqual(ZsxqTopic.query.count(), 0); self.assertEqual(ZsxqSyncRun.query.count(), 0)


class JsonImportApiTest(unittest.TestCase):
    def setUp(self):
        from apis.v1.zsxq_json_import import bp
        self.app = Flask(__name__); self.app.testing = True; self.app.register_blueprint(bp)
        self.client = self.app.test_client()
    def test_requires_admin(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with patch('utils.auth_utils.get_current_user', return_value=None):
            self.assertEqual(self.client.post('/api/v1/zsxq/json-import/preview').status_code, 401)
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(is_admin=False)):
            self.assertEqual(self.client.post('/api/v1/zsxq/json-import/preview').status_code, 403)
    def test_upload_preview_and_commit_require_matching_file(self):
        import io
        import hashlib
        from unittest.mock import patch
        from types import SimpleNamespace
        raw = json.dumps(capture()).encode()
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(is_admin=True)):
            with patch('apis.v1.zsxq_json_import.preview_capture', return_value={'unique_topics': 1}):
                response = self.client.post('/api/v1/zsxq/json-import/preview', data={'file': (io.BytesIO(raw), 'capture.json')})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['data']['file_sha256'], hashlib.sha256(raw).hexdigest())
            with patch('apis.v1.zsxq_json_import.db.session.rollback'), patch('apis.v1.zsxq_json_import.import_capture') as commit:
                response = self.client.post('/api/v1/zsxq/json-import/commit', data={'file': (io.BytesIO(raw), 'capture.json'), 'file_sha256': 'wrong'})
                self.assertEqual(response.status_code, 400); commit.assert_not_called()

if __name__ == '__main__': unittest.main()
