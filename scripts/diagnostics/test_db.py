import sys
import os
sys.path.append('src/backend')
from app import create_app
from models.task import PublicationTask

app = create_app()
with app.app_context():
    task = PublicationTask.query.order_by(PublicationTask.created_at.desc()).first()
    print(f"ID: {task.id}")
    print(f"Title: {task.params.get('bilibili', {}).get('title')}")
    print(f"Status: {task.status}")
    print(f"Celery ID: {task.celery_task_id}")
