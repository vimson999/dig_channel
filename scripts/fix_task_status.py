import os
import sys
from dotenv import load_dotenv

# Ensure the backend directory is in sys.path
backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

load_dotenv()

from app import create_app
from models import db
from models.task import PublicationTask

app = create_app()

def fix_tasks():
    # IDs to fix (partial matches from user)
    target_ids = ['50a01208', 'f96c43c5']
    
    with app.app_context():
        for partial_id in target_ids:
            task = PublicationTask.query.filter(PublicationTask.id.like(f"{partial_id}%")).first()
            if task:
                print(f"Fixing Task {task.id}:")
                print(f"  Old status: {task.status}, Current step: {task.current_step}")
                
                # Reset to a clean 'completed' state so the frontend shows the "Supplement YT" button
                task.status = 'completed'
                task.current_step = 'done'
                task.progress = 100
                
                # Remove any failed youtube indicators from result to enable the button
                if task.result and 'youtube_error' in task.result:
                    new_result = dict(task.result)
                    del new_result['youtube_error']
                    task.result = new_result
                
                if task.result and 'youtube_result' in task.result:
                    new_result = dict(task.result)
                    del new_result['youtube_result']
                    task.result = new_result
                
                task.error_message = None
                db.session.commit()
                print(f"  ✅ Fixed successfully.")
            else:
                print(f"⚠️ Task starting with {partial_id} not found.")

if __name__ == "__main__":
    fix_tasks()
