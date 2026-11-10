import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/backend'))
from models import db
from models.x_content import MODELS
from services.x_content_service import import_bundle, save_record, serialize, seed_mock, clear_mock

class XContentTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', TESTING=True)
        db.init_app(self.app)
        self.ctx = self.app.app_context(); self.ctx.push()
        for model in MODELS.values(): model.__table__.create(db.engine)
    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.ctx.pop()
    def test_metrics_unknown_and_zero(self):
        item = save_record('cases', {'id':'C1','raw_text':'例子','views':2265,'replies':187,'author_followers':513})
        self.assertEqual(serialize(item)['reply_rate'], 8.26)
        self.assertEqual(serialize(item)['follower_leverage'], 4.42)
        item = save_record('cases', {'id':'C2','raw_text':'未知','views':0})
        self.assertIsNone(serialize(item)['reply_rate'])
    def test_import_atomic_and_refs(self):
        with self.assertRaises(ValueError):
            import_bundle({'patterns':[{'id':'P1','name':'套路'}], 'posts':[{'id':'O1','content':'正文','pattern_id':'missing'}]})
        self.assertEqual(MODELS['patterns'].query.count(), 0)
        import_bundle({'patterns':[{'id':'P1','name':'套路'}], 'cases':[{'id':'C1','raw_text':'案例','pattern_id':'P1'}], 'posts':[{'id':'O1','content':'正文','ref_case_ids':['C1']}]})
        self.assertEqual(MODELS['posts'].query.count(), 1)
    def test_mock_cleanup_and_real_import_rollback(self):
        seed_mock(); counts = [m.query.count() for m in MODELS.values()]; seed_mock()
        self.assertEqual(counts, [m.query.count() for m in MODELS.values()])
        save_record('patterns', {'id':'REAL','name':'真实数据'})
        with self.assertRaises(ValueError): import_bundle({'posts':[{'id':'BAD','content':'正文','ref_case_ids':['missing']}]}, replace_mock=True)
        self.assertEqual(counts[1], MODELS['cases'].query.count())
        import_bundle({'patterns':[{'id':'P1','name':'真实套路'}]}, replace_mock=True)
        self.assertEqual(MODELS['patterns'].query.count(), 2)
        self.assertEqual(MODELS['cases'].query.count(), 0)
    def test_validation(self):
        for data in ({'raw_text':'a','views':-1}, {'raw_text':'a','views':True}, {'raw_text':'a','replies':1.2}, {'raw_text':'a','is_negative_sample':'false'}):
            with self.assertRaises(ValueError): save_record('cases', data)
        with self.assertRaises(ValueError): save_record('posts', {'content':'a','ref_case_ids':'C1'})
        with self.assertRaises(ValueError): import_bundle({'unknown':[]}, replace_mock=True)
    def test_original_export_mapping_and_missing_sources(self):
        original={'meta':{'owner_account':'vimson999'},'x_viral_patterns':[{'pattern_code':'P1','name':'茶馆','category':'cold_start','tags':['茶'],'key_takeaway':'来源观点'}],
          'x_viral_cases':[{'case_code':'C1','pattern_code':'P1','raw_text':'原文','is_negative_sample':0,'published_at':'2026-10-25 11:47:00','views':100,'replies':8,'reply_rate':999}],
          'x_own_posts':[{'post_code':'O1','content':'正文','status':'ready_draft','ref_case_codes':['C1','MISSING']}],
          'x_reply_tactics':[{'tactic_code':'T1','tactic_name':'防范','scenario_type':'bot_defense','sample_replies':['保持礼貌'],'matched_handles':['@friend']}]}
        import_bundle(original)
        case=db.session.get(MODELS['cases'],'C1');post=db.session.get(MODELS['posts'],'O1')
        self.assertEqual(serialize(case)['published_at'],'2026-10-25T03:47:00Z')
        self.assertEqual(serialize(case)['reply_rate'],8)
        self.assertEqual(post.ref_case_ids,['C1']);self.assertEqual(post.unresolved_case_ids,['MISSING'])
        self.assertEqual(post.status,'draft_variant')
        self.assertEqual(db.session.get(MODELS['tactics'],'T1').action_type,'block')
        import_bundle(original)
        self.assertEqual(MODELS['posts'].query.count(),1)
    def test_export_preserves_explicit_action_and_multiline_replies(self):
        bundle={'x_reply_tactics':[{'tactic_code':'T1','tactic_name':'忽略营销','action_type':'ignore','sample_replies':['第一段\n第二段','另一条回复']}]}
        import_bundle(bundle)
        row=db.session.get(MODELS['tactics'],'T1')
        self.assertEqual(row.action_type,'ignore')
        self.assertEqual(row.sample_replies,['第一段\n第二段','另一条回复'])
        import_bundle({'x_reply_tactics':[{'tactic_code':'T1','tactic_name':'修改名称'}]})
        self.assertEqual(row.action_type,'ignore')
    def test_real_references_prevent_mock_cleanup(self):
        seed_mock()
        save_record('posts',{'id':'REAL','content':'正文','ref_case_ids':['MOCK-CASE-01']})
        with self.assertRaises(ValueError):clear_mock()
        self.assertEqual(MODELS['cases'].query.count(),1)
    def test_migration_matches_models(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine,inspect
        path=Path(__file__).resolve().parents[2]/'src/backend/migrations/versions/20261027_x_content.py'
        spec=importlib.util.spec_from_file_location('x_migration',path)
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        engine=create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            migration.op=Operations(MigrationContext.configure(connection))
            migration.upgrade();migration.upgrade()
            for model in MODELS.values():
                self.assertEqual({c['name'] for c in inspect(connection).get_columns(model.__tablename__)},set(model.__table__.columns.keys()))
            migration.downgrade()
            self.assertEqual(inspect(connection).get_table_names(),[])
        engine.dispose()
    def test_auth_and_api(self):
        from apis.v1.x_content import bp
        self.app.register_blueprint(bp); client = self.app.test_client()
        with patch('utils.auth_utils.get_current_user', return_value=None):
            self.assertEqual(client.get('/api/v1/x-content/patterns').status_code, 401)
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(is_admin=False)):
            self.assertEqual(client.post('/api/v1/x-content/mock').status_code,403)
        with patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(is_admin=True)):
            self.assertEqual(client.post('/api/v1/x-content/mock').status_code,200)
            result=client.get('/api/v1/x-content/cases?q=示例').json['data']
            self.assertGreater(result['total'],0)
            self.assertEqual(client.get('/api/v1/x-content/cases?page=0').status_code,400)
            self.assertEqual(client.post('/api/v1/x-content/posts',json={'content':'新草稿'}).status_code,200)
            self.assertEqual(client.get('/api/v1/x-content/summary').status_code,200)
if __name__ == '__main__': unittest.main()
