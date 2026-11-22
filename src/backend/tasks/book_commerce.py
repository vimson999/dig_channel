from celery_config import celery_app
from datetime import datetime

from models import db
from models.book_commerce import AnalysisRun
from services.book_commerce.analysis import RunInProgress, execute_pending


@celery_app.task(bind=True, name='tasks.book_commerce.calculate', max_retries=12)
def calculate(self, run_id):
    from app import create_app
    app = create_app(register_apis=False)
    with app.app_context():
        if not app.config.get('ENABLE_BOOK_COMMERCE', False):
            (db.session.query(AnalysisRun).filter_by(id=run_id, status='pending')
             .update({'status': 'failed', 'error_code': 'feature_disabled', 'completed_at': datetime.utcnow()}, synchronize_session=False))
            db.session.commit()
            return False
        try:
            return execute_pending(db.session, run_id)
        except RunInProgress as exc:
            raise self.retry(exc=exc, countdown=15)
