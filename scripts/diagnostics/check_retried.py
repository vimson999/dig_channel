import sys
import os
sys.path.append('src/backend')
from app import create_app
from models.task import PublicationTask

app = create_app()
with app.app_context():
    task = PublicationTask.query.filter_by(id="eb3ffd28-3d9c-44b2-991b-d68553eed6b8").first()
    print(f"Status: {task.status}")
    print(f"Progress: {task.progress}")
