import sys
import os
sys.path.append('src/backend')
from celery_config import celery_app
from app import create_app
from models import db
from sqlalchemy import text

@celery_app.task
def test_db_connection():
    app = create_app()
    with app.app_context():
        try:
            result = db.session.execute(text("SELECT 1")).scalar()
            return f"Success! Result: {result}"
        except Exception as e:
            return f"Failed: {e}"

if __name__ == "__main__":
    task = test_db_connection.delay()
    print(f"Submitted test task. ID: {task.id}")
    print(f"Result: {task.get(timeout=10)}")
