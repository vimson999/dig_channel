from pathlib import Path
import sys
from unittest.mock import patch
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from services.backend_process_bootstrap import _bootstrap_celery_services


def test_disabled_book_commerce_does_not_start_worker(monkeypatch):
    app = Flask(__name__)
    app.config['ENABLE_BOOK_COMMERCE'] = False
    monkeypatch.setenv('BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES', 'book_commerce')
    with patch('apis.v1.celery_monitor._start_service') as start, \
         patch('apis.v1.celery_monitor._service_status', return_value={'running':False}), \
         patch('apis.v1.celery_monitor._is_service_paused', return_value=False):
        result = _bootstrap_celery_services(app, 'ensure')
    start.assert_not_called()
    assert result[0]['skipped'] is True


def test_monitor_cannot_start_disabled_book_commerce_worker():
    import pytest
    from apis.v1.celery_monitor import SERVICES, _start_service
    app = Flask(__name__)
    app.config['ENABLE_BOOK_COMMERCE'] = False
    with app.app_context(), patch('apis.v1.celery_monitor._ensure_dirs') as ensure, patch('apis.v1.celery_monitor.subprocess.Popen') as spawn:
        with pytest.raises(RuntimeError, match='未启用'):
            _start_service('book_commerce', SERVICES['book_commerce'])
    ensure.assert_not_called()
    spawn.assert_not_called()
