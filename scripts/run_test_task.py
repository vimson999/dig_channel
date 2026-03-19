
import os
import sys
import uuid
import json
from datetime import datetime

# Add src/backend to path
backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app import create_app
from models import db
from models.task import PublicationTask
from tasks.publisher import run_publication_pipeline

def run_test():
    app = create_app()
    with app.app_context():
        # Define test parameters
        # Audio file: t1_1773741301070.mp3 (35s)
        # Background: assets/cover/Gemini_Generated_Image_ifcvkkifcvkkifcv.png
        
        audio_rel_path = "data/uploads/t1_1773741301070.mp3"
        image_rel_path = "assets/cover/Gemini_Generated_Image_ifcvkkifcvkkifcv.png"
        
        params = {
            "video": {
                "enabled": True,
                "audio_path": audio_rel_path,
                "image_path": image_rel_path,
                "asr_enabled": True,
                "sub_burn_enabled": True,
                "title": "35s测试视频 - 智能分句与硬件加速验证"
            },
            "vertical_video": {
                "enabled": True,
                "image_path": image_rel_path,
                "asr_enabled": True,
                "sub_burn_enabled": True
            },
            "thumbnail": {
                "main_title": "35s测试验证\n智能分句与硬件加速",
                "sub_title": "Subtitle System Refactor Test",
                "bg_index": 0,
                "layout": "horizontal_reverse",
                "custom_bg_path": image_rel_path,
                "date_text": datetime.now().strftime("%Y-%m-%d"),
                "tag_text": "TEST"
            },
            "bilibili": {
                "title": "35s测试视频 - 智能分句与硬件加速验证",
                "desc": "Automated test for MLX-Whisper and Subtitle scrolling."
            }
        }
        
        task = PublicationTask(
            id=str(uuid.uuid4()),
            params=params,
            status='pending',
            current_step='init'
        )
        
        db.session.add(task)
        db.session.commit()
        
        task_id = task.id
        print(f"🚀 Created Task in DB: {task_id}")
        
        # Trigger Celery task
        # We use .delay() to send it to the worker
        celery_result = run_publication_pipeline.delay(task_id)
        
        task.celery_task_id = celery_result.id
        db.session.commit()
        
        print(f"📡 Sent to Celery Worker. Celery Task ID: {celery_result.id}")
        print(f"📊 You can monitor logs now.")

if __name__ == "__main__":
    run_test()
