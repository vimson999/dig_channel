
import os
import sys

# Add src/backend to path
backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app import create_app
from models import db
from models.task import PublicationTask

def check_status(task_id):
    app = create_app()
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            print(f"❌ Task {task_id} not found.")
            return

        print(f"📊 Task ID: {task.id}")
        print(f"🔗 Celery ID: {task.celery_task_id}")
        print(f"📌 Status: {task.status}")
        print(f"🚶 Current Step: {task.current_step}")
        print(f"📈 Progress: {task.progress}%")
        if task.error_message:
            print(f"❌ Error: {task.error_message}")
        if task.result:
            import json
            print(f"🎁 Result: {json.dumps(task.result, indent=2, ensure_ascii=False)}")
        
        print(f"🕒 Updated At: {task.updated_at}")

if __name__ == "__main__":
    import sys
    tid = sys.argv[1] if len(sys.argv) > 1 else "c604facc-e0c0-4aa4-b9a6-31d77fe019d5"
    check_status(tid)
