from pathlib import Path
import sys
from unittest.mock import patch

from flask import Flask
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from models import db
from models.book_commerce import AnalysisRun
from tasks.book_commerce import calculate


def test_disabled_worker_does_not_execute_pending_task():
    app = Flask(__name__)
    app.config.update(ENABLE_BOOK_COMMERCE=False, SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        from datetime import date
        run = AnalysisRun(start_date=date(2026, 11, 18), end_date=date(2026, 11, 18), status='pending',
                          input_revision=0, rules_version=1, rules_config={}, calculation_version='1')
        db.session.add(run)
        db.session.commit()
        run_id = run.id
        with patch('app.create_app', return_value=app), patch('tasks.book_commerce.execute_pending') as execute:
            assert calculate.run(run_id) is False
        execute.assert_not_called()
        db.session.expire_all()
        assert db.session.get(AnalysisRun, run_id).error_code == 'feature_disabled'
        db.session.remove()
        db.drop_all()
