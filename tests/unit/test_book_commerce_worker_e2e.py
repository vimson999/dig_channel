"""Exercise the actual Celery worker with disposable local Postgres and Redis."""
import os
import copy
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'backend'))
sys.path.insert(0, str(ROOT / 'tests' / 'unit'))
sys.path.insert(0, str(ROOT / 'scripts' / 'maintenance'))
from models import User, db
from models.book_commerce import State
from apis.v1.book_commerce import bp
from test_book_commerce_ingest import commerce_payload


def test_real_worker_processes_queued_analysis(tmp_path):
    url = os.environ.get('BOOK_COMMERCE_TEST_DATABASE_URL')
    broker = os.environ.get('BOOK_COMMERCE_TEST_REDIS_URL')
    if not url or not broker:
        pytest.skip('requires disposable PostgreSQL and Redis')
    parsed = make_url(url)
    if parsed.host not in ('localhost', '127.0.0.1') or not parsed.database.startswith('bc_test_') or not broker.startswith('redis://127.0.0.1:'):
        raise RuntimeError('worker test requires disposable local services')
    app = Flask(__name__, static_folder=str(ROOT / 'src' / 'frontend'), static_url_path='')
    app.config.update(TESTING=True, ENABLE_BOOK_COMMERCE=True, SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False, SECRET_KEY='test')
    db.init_app(app)
    app.register_blueprint(bp)
    with app.app_context():
        tables = [t for t in db.metadata.sorted_tables if t.name == 'users' or t.name.startswith('bc_')]
        User.__table__.create(db.engine)
        from migrate_book_commerce import upgrade
        upgrade(db.engine)
        db.session.add(User(username='test', email='test@example.com', password_hash='unused'))
        db.session.commit()
    worker_env = dict(os.environ, DATABASE_URL=url, CELERY_BROKER_URL=broker, CELERY_RESULT_BACKEND=broker,
                      REDIS_URL=broker, FLASK_ENV='testing', AUTH_MODE='local', DB_PROCESS_ROLE='worker',
                      BACKEND_PROCESS_BOOTSTRAP_ENABLED='0', ENABLE_BOOK_COMMERCE='1', PYTHONPATH=str(ROOT / 'src' / 'backend'))
    log = tmp_path / 'worker.log'
    with log.open('w+') as log_handle:
        worker = subprocess.Popen([str(ROOT/'venv/bin/celery'), '-A', 'celery_config.celery_app', 'worker', '-P', 'threads',
                                   '-c', '1', '-Q', 'book_commerce', '--hostname=bc_test@%h', '--without-gossip', '--without-mingle', '-l', 'warning'],
                                  cwd=ROOT/'src/backend', env=worker_env, stdout=log_handle, stderr=subprocess.STDOUT)
        try:
            from celery import Celery
            probe = Celery('probe', broker=broker)
            ready = False
            for _ in range(15):
                queues = probe.control.inspect(timeout=0.7).active_queues() or {}
                if any(any(q.get('name') == 'book_commerce' for q in value) for value in queues.values()):
                    ready = True
                    break
                if worker.poll() is not None:
                    break
                time.sleep(0.5)
            if not ready:
                log_handle.flush()
                pytest.fail('worker failed to become ready: ' + log.read_text()[-3000:])
            from services.book_commerce import ingest
            from datetime import datetime, timezone
            with app.app_context(), patch('utils.auth_utils.get_current_user', return_value=SimpleNamespace(id=1, is_admin=True)), \
                 patch('celery_config.celery_app', probe), patch.object(ingest, '_utcnow', return_value=datetime(2026, 11, 19, tzinfo=timezone.utc)):
                from threading import Thread
                from werkzeug.serving import make_server
                from playwright.sync_api import sync_playwright
                server = make_server('127.0.0.1', 0, app, threaded=True)
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with sync_playwright() as playwright:
                        chrome = os.environ.get('BOOK_COMMERCE_TEST_CHROME', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                        if not Path(chrome).is_file():
                            pytest.fail('browser executable is unavailable for UI verification')
                        browser = playwright.chromium.launch(headless=True, executable_path=chrome)
                        page = browser.new_page(viewport={'width': 1440, 'height': 900})
                        page.route('**/js/env.js', lambda route: route.fulfill(status=200, content_type='application/javascript', body="window.API_BASE_URL='/api/v1';localStorage.setItem('access_token','test');"))
                        page.route('**/api/v1/auth/verify', lambda route: route.fulfill(status=200, json={'success':True,'data':{'user':{'id':1,'is_admin':True,'username':'test'}}}))
                        page.goto(f'http://127.0.0.1:{server.server_port}/book-commerce.html', wait_until='domcontentloaded')
                        page.wait_for_function("document.querySelector('#summary')?.children.length === 4")
                        page.get_by_role('button', name='数据导入').click()
                        first = commerce_payload()
                        second = copy.deepcopy(first)
                        for group in ('accounts', 'books', 'listings', 'videos', 'account_listings', 'observations'):
                            for row in second[group]:
                                row['observed_at'] = '2026-11-18T12:00:00+08:00'
                        second['video_listing_links'] = []
                        second['observations'][0]['values']['cumulative_sales'] = 335
                        second['observations'][1]['values']['shares'] = 380
                        for index, item in enumerate((first, second)):
                            page.locator('input[type=file]').set_input_files({'name':f'data-{index}.json', 'mimeType':'application/json', 'buffer':json.dumps(item,ensure_ascii=False).encode()})
                            page.get_by_role('button', name='预览文件').click()
                            page.get_by_text('新增观测 2 条，重复 0 条。').wait_for()
                            page.get_by_role('button', name='确认导入').click()
                            page.get_by_text('导入完成：新增 2 条，重复 0 条。').wait_for()
                        date_inputs = page.locator('#content input[type=date]')
                        date_inputs.nth(0).fill('2026-11-18')
                        date_inputs.nth(1).fill('2026-11-18')
                        page.get_by_role('button', name='开始计算').click()
                        page.wait_for_function("document.querySelector('#notice')?.textContent === '计算完成'", timeout=30000)
                        page.get_by_role('button', name='候选池').click()
                        page.locator('#business-date').fill('2026-11-18')
                        page.get_by_role('button', name='查看情报').click()
                        page.get_by_text('测试视频', exact=True).wait_for()
                        page.get_by_role('button', name='查看依据').click()
                        assert '310' in page.locator('#detail-body').inner_text()
                        assert '380' in page.locator('#detail-body').inner_text()
                        page.get_by_role('button', name='关闭', exact=True).click()
                        page.on('dialog', lambda dialog: dialog.accept('先观察'))
                        page.get_by_role('button', name='忽略', exact=True).click()
                        page.locator('#content .empty').wait_for()
                        page.get_by_role('button', name='我的标记').click()
                        page.get_by_text('先观察', exact=False).wait_for()
                        assert '已忽略' in page.locator('#content').inner_text()
                        browser.close()
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
    with app.app_context():
        db.session.remove()
        db.metadata.drop_all(db.engine, tables=tables)
        db.engine.dispose()
