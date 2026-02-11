import os
import sys
from dotenv import load_dotenv

# Ensure the backend directory is in sys.path
backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

load_dotenv()

# Setup Flask App context
from app import create_app
from models import db
from models.task import PublicationTask

app = create_app()

with app.app_context():
    tasks = PublicationTask.query.order_by(PublicationTask.created_at.desc()).limit(5).all()
    print(f"Found {len(tasks)} recent tasks:")
    for t in tasks:
        print(f"ID: {t.id}")
        print(f"  Status: {t.status}")
        print(f"  Current Step: {t.current_step}")
        print(f"  Progress: {t.progress}%")
        print(f"  YouTube in Params: {'youtube' in t.params}")
        if 'youtube' in t.params:
            print(f"  YouTube Params: {t.params.get('youtube')}")
        print(f"  Result: {t.result}")
        print(f"  Created At: {t.created_at}")
        print("-" * 20)
