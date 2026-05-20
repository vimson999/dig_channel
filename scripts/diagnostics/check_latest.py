import sys
import os
sys.path.append('src/backend')
from app import create_app
from models.task import PublicationTask

app = create_app()
with app.app_context():
    task = PublicationTask.query.filter_by(id="dbbdf1ba-48ac-45e3-b773-12d4eba3f1b1").first()
    print(f"Status: {task.status}")
    print(f"Current Step: {task.current_step}")
    print(f"Progress: {task.progress}%")
