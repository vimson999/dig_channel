import sys
import os
sys.path.append('src/backend')
from app import create_app
from models.task import PublicationTask

app = create_app()
with app.app_context():
    tasks = PublicationTask.query.order_by(PublicationTask.created_at.desc()).limit(3).all()
    for task in tasks:
        print(f"ID: {task.id} | Status: {task.status} | Celery ID: {task.celery_task_id} | Created: {task.created_at}")
