import copy
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/backend'))
from models import db
from models.shengcai_project import ShengcaiProject, ShengcaiProjectImport
from services.shengcai_project_service import import_catalog, list_catalog, validate_catalog


def source():
    return {'success': True, 'status': 0, 'data': {'total': 2, 'items': [
        {'id': 1, 'name': 'AI 视频', 'summary': '制作视频', 'incomeMin': 10, 'incomeMax': 100,
         'dailyMinutes': 120, 'estimatedCost': 0, 'gmtPublish': 1792501293, 'likeCount': 5,
         'highlightText': '低成本|内容制作', 'caseCount': 2, 'resourceCount': 3,
         'platformMenus': [{'id': 1, 'name': 'YouTube'}], 'monetizeMenus': [{'id': 4, 'name': '创作者分成'}],
         'crowdMenus': [{'id': 5, 'name': '内容制作'}]},
        {'id': 2, 'name': '模板销售', 'summary': '销售模板', 'estimatedCost': 300,
         'dailyMinutes': 60, 'gmtPublish': 1770004800, 'likeCount': 8,
         'platformMenus': [{'id': 2, 'name': '小红书'}], 'monetizeMenus': [], 'crowdMenus': []}
    ]}}


class ProjectCatalogTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False)
        db.init_app(self.app); self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()
    def test_import_preserves_source_and_survives_new_session(self):
        result = import_catalog(source(), 'list.json')
        self.assertEqual(result['new'], 2); db.session.remove()
        self.assertEqual(ShengcaiProject.query.count(), 2)
        self.assertEqual(db.session.get(ShengcaiProject, '1').raw_payload, source()['data']['items'][0])
        self.assertEqual(ShengcaiProjectImport.query.one().source_filename, 'list.json')
    def test_reimport_is_idempotent_and_updates_by_source_id(self):
        import_catalog(source())
        repeat = import_catalog(source()); self.assertEqual(repeat['unchanged'], 2)
        changed = source(); changed['data']['items'][0]['likeCount'] = 19
        result = import_catalog(changed)
        self.assertEqual(result['updated'], 1); self.assertEqual(result['new'], 0)
        self.assertEqual(ShengcaiProject.query.count(), 2)
        self.assertEqual(db.session.get(ShengcaiProject, '1').raw_payload['likeCount'], 19)
    def test_invalid_input_never_partially_writes(self):
        for change in ('duplicate', 'name', 'numeric', 'tag', 'success', 'total'):
            x = source()
            if change == 'duplicate': x['data']['items'][1]['id'] = 1
            elif change == 'name': x['data']['items'][1]['name'] = ''
            elif change == 'numeric': x['data']['items'][1]['estimatedCost'] = -1
            elif change == 'tag': x['data']['items'][1]['platformMenus'] = ['bad']
            elif change == 'success': x['success'] = False
            else: x['data']['total'] = 1
            with self.subTest(change=change), self.assertRaises(ValueError): import_catalog(x)
        self.assertEqual(ShengcaiProject.query.count(), 0)
    def test_partial_snapshot_does_not_delete_existing_projects(self):
        import_catalog(source()); x = source(); x['data']['items'] = x['data']['items'][:1]
        import_catalog(x); self.assertEqual(ShengcaiProject.query.count(), 2)
    def test_filters_sort_pagination_and_global_facets(self):
        import_catalog(source())
        r = list_catalog(q='视频', platform='YouTube', monetize='创作者分成', crowd='内容制作')
        self.assertEqual(r['total'], 1); self.assertEqual(r['items'][0]['id'], 1)
        self.assertEqual(r['catalog_total'], 2)
        self.assertEqual({t['name'] for t in r['facets']['platforms']}, {'YouTube', '小红书'})
        r = list_catalog(sort='likes', page=1, per_page=1)
        self.assertEqual(r['items'][0]['id'], 2); self.assertTrue(r['has_next'])
        self.assertEqual(list_catalog(q='不存在')['total'], 0)
    def test_income_sorts_use_correct_bound_and_keep_missing_values_last(self):
        x = source()
        x['data']['items'][1].update(incomeMin=70, incomeMax=80)
        third = copy.deepcopy(x['data']['items'][0]); third.update(id=3, incomeMin=20, incomeMax=900)
        fourth = copy.deepcopy(x['data']['items'][1]); fourth['id'] = 4
        fourth.pop('incomeMin'); fourth.pop('incomeMax')
        x['data']['items'].extend([third, fourth]); x['data']['total'] = 4
        import_catalog(x)
        self.assertEqual([i['id'] for i in list_catalog(sort='income_max')['items']], [3, 1, 2, 4])
        self.assertEqual([i['id'] for i in list_catalog(sort='income_min')['items']], [2, 3, 1, 4])

    def test_configurable_page_size_keeps_page_boundaries_correct(self):
        x = source(); template = x['data']['items'][0]
        x['data']['items'] = [dict(copy.deepcopy(template), id=i) for i in range(1, 18)]
        x['data']['total'] = 17; import_catalog(x)
        first = list_catalog(sort='income_max', page=1, per_page=12)
        second = list_catalog(sort='income_max', page=2, per_page=12)
        self.assertEqual(len(first['items']), 12); self.assertTrue(first['has_next'])
        self.assertEqual(len(second['items']), 5); self.assertFalse(second['has_next'])
        self.assertEqual({i['id'] for i in first['items']} & {i['id'] for i in second['items']}, set())
        self.assertEqual(len(list_catalog(per_page=48)['items']), 17)

    def test_compound_sort_uses_primary_then_secondary_before_pagination(self):
        x = source()
        template = x['data']['items'][0]
        x['data']['items'] = [dict(copy.deepcopy(template), id=identity, gmtPublish=published,
                                 incomeMax=income)
                              for identity, published, income in
                              [(1, 200, 100), (2, 200, 900), (3, 100, 900), (4, 200, None)]]
        x['data']['total'] = 4
        import_catalog(x)
        ordered = list_catalog(sort='newest', secondary_sort='income_max')
        self.assertEqual([i['id'] for i in ordered['items']], [2, 1, 4, 3])
        swapped = list_catalog(sort='income_max', secondary_sort='newest')
        self.assertEqual([i['id'] for i in swapped['items']], [2, 3, 1, 4])
        self.assertEqual(list_catalog(sort='newest', secondary_sort='income_max',
                                      page=2, per_page=1)['items'][0]['id'], 1)
        self.assertEqual(list_catalog(sort='newest', secondary_sort='newest')['items'],
                         list_catalog(sort='newest')['items'])
        with self.assertRaises(ValueError):
            list_catalog(secondary_sort='invalid')

    def test_schema_migration_can_initialize_and_repeat(self):
        import importlib.util
        from sqlalchemy import create_engine, inspect
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[2] / 'src/backend/migrations/versions/20261021_shengcai_catalog.py'
        spec = importlib.util.spec_from_file_location('catalog_migration', path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade(); migration.upgrade()
            for model in (ShengcaiProject, ShengcaiProjectImport):
                columns = {c['name'] for c in inspect(connection).get_columns(model.__tablename__)}
                self.assertEqual(columns, set(model.__table__.columns.keys()))
        engine.dispose()

    def test_recent_name_filter_runs_before_income_sort_and_pagination(self):
        now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        stamp = now.timestamp()
        template = source()['data']['items'][0]
        rows = [dict(copy.deepcopy(template), id=i, name=name, summary='AI 视频',
                     gmtPublish=published, incomeMax=income)
                for i, name, published, income in [
                    (1, 'AI 新项目', stamp - 86400, 100),
                    (2, 'AI 边界项目', stamp - 30 * 86400, 900),
                    (3, 'AI 旧项目', stamp - 30 * 86400 - 1, 9000),
                    (4, '名称不匹配', stamp - 86400, 8000),
                    (5, 'AI 未发布', stamp + 1, 7000),
                    (6, 'AI 时间未知', None, 6000)]]
        import_catalog({'success': True, 'data': {'items': rows, 'total': 6}})
        with patch('services.shengcai_project_service.datetime') as clock:
            clock.now.return_value = now
            result = list_catalog(published_within='30', name='ai', sort='income_max', per_page=1)
            self.assertEqual(result['total'], 2)
            self.assertEqual(result['items'][0]['id'], 2)
            self.assertTrue(result['has_next'])
            self.assertEqual(list_catalog(published_within='30', name='ai', sort='income_max',
                                         per_page=1, page=2)['items'][0]['id'], 1)
            self.assertEqual(list_catalog(published_within='all')['total'], 6)
            self.assertEqual(list_catalog(published_within='30')['total'], 3)
        with self.assertRaises(ValueError):
            list_catalog(published_within='invalid')

    def test_transaction_failure_rolls_back(self):
        with patch.object(db.session, 'commit', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError): import_catalog(source())
        self.assertEqual(ShengcaiProject.query.count(), 0)
    def test_api_auth_upload_and_list(self):
        from apis.v1.shengcai_projects import bp
        self.app.register_blueprint(bp); client = self.app.test_client()
        with patch('utils.auth_utils.get_current_user', return_value=None):
            self.assertEqual(client.get('/api/v1/shengcai-projects').status_code, 401)
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(is_admin=False)):
            self.assertEqual(client.post('/api/v1/shengcai-projects/import').status_code, 403)
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(is_admin=True)):
            response = client.post('/api/v1/shengcai-projects/import', data={'file': (io.BytesIO(json.dumps(source()).encode()), 'list.json')})
            self.assertEqual(response.status_code, 200); self.assertEqual(response.json['data']['new'], 2)
            response = client.get('/api/v1/shengcai-projects?platform=YouTube')
            self.assertEqual(response.json['data']['total'], 1)
            self.assertEqual(client.get('/api/v1/shengcai-projects?per_page=10000').status_code, 400)
            self.assertEqual(client.get('/api/v1/shengcai-projects?sort=bad').status_code, 400)
            self.assertEqual(client.get('/api/v1/shengcai-projects?secondary_sort=bad').status_code, 400)
            self.assertEqual(client.get('/api/v1/shengcai-projects?published_within=bad').status_code, 400)
            with patch('apis.v1.shengcai_projects.list_catalog', return_value={}) as listing:
                response = client.get('/api/v1/shengcai-projects?sort=cost&secondary_sort=income_max&published_within=30&name=AI')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(listing.call_args.kwargs['sort'], 'cost')
                self.assertEqual(listing.call_args.kwargs['secondary_sort'], 'income_max')
                self.assertEqual(listing.call_args.kwargs['published_within'], '30')
                self.assertEqual(listing.call_args.kwargs['name'], 'AI')

if __name__ == '__main__': unittest.main()
