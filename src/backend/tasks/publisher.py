import asyncio
import os
import sys
import logging
from datetime import datetime

# Ensure src/backend is in sys.path for Celery worker
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app
from models.task import PublicationTask
from models import db
from services.video_editor import video_editor_service
from services.thumbnail_service import thumbnail_service
from services.bilibili.uploader import BiliUploader
from config import get_config

logger = logging.getLogger(__name__)

def to_abs_path(rel_path):
    """Helper to convert relative path to project-root-based absolute path."""
    if not rel_path: return None
    if os.path.isabs(rel_path): return rel_path
    # Root is 4 levels up: tasks/publisher.py -> backend -> src -> root
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base_dir, rel_path)

def sanitize_filename(name):
    """Sanitize filename to keep Chinese characters but remove illegal fs chars."""
    import re
    # Remove / \ : * ? " < > |
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # Replace spaces with underscores
    name = name.replace(" ", "_")
    return name.strip()

@celery_app.task(bind=True)
def run_publication_pipeline(self, task_id):
    """
    Execute the full publication pipeline: Synthesis -> Thumbnail -> Upload.
    """
    from app import create_app
    app = create_app()
    
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found in DB")
            return

        try:
            # --- Prepare Naming ---
            params = task.params
            bili_params = params.get('bilibili', {})
            thumb_params = params.get('thumbnail', {})
            
            # Determine base filename from Title -> (Main Title lines + Sub Title) -> Task ID
            raw_title = bili_params.get('title')
            if not raw_title:
                # 自动合成逻辑：主标题各行 + 副标题
                main_lines = [l.strip() for l in thumb_params.get('main_title', '').split('\n') if l.strip()]
                sub_text = thumb_params.get('sub_title', '').strip()
                if sub_text:
                    main_lines.append(sub_text)
                raw_title = " | ".join(main_lines) if main_lines else ""

            if raw_title:
                base_name = sanitize_filename(raw_title)
                # Add short task_id suffix to ensure uniqueness if title repeats
                base_name = f"{base_name}_{task_id[:6]}"
            else:
                base_name = f"pub_{task_id}"

            # --- Step 1: Synthesis ---
            task.status = 'processing'
            task.current_step = 'synthesis'
            task.progress = 10
            db.session.commit()
            
            logger.info(f"Task {task_id}: Starting video synthesis...")
            
            # Extract and Absolute-ify Video Params
            vid_params = params.get('video', {})
            video_path = to_abs_path(vid_params.get('video_path'))
            image_path = to_abs_path(vid_params.get('image_path'))
            audio_path = to_abs_path(vid_params.get('audio_path'))
            video_c_path = to_abs_path(vid_params.get('video_c_path'))
            audio_c_path = to_abs_path(vid_params.get('audio_c_path'))
            
            audio_start = float(vid_params.get('audio_start', 7.0))
            audio_end_offset = float(vid_params.get('audio_end_offset', 10.0))
            
            # Synthesis
            final_video_path = video_editor_service.process_mixed_content(
                video_file=video_path,
                image_file=image_path,
                audio_file=audio_path,
                video_c_path=video_c_path,
                audio_c_path=audio_c_path,
                audio_start=audio_start,
                audio_end_offset=audio_end_offset,
                output_filename=f"{base_name}.mp4"
            )
            
            task.result = task.result or {}
            task.result['video_path'] = final_video_path
            task.progress = 50
            db.session.commit()

            # --- Step 2: Thumbnail ---
            task.current_step = 'thumbnail'
            db.session.commit()
            
            logger.info(f"Task {task_id}: Generating thumbnail...")
            
            final_cover_path = thumbnail_service.generate_thumbnail(
                main_title=thumb_params.get('main_title'),
                sub_title=thumb_params.get('sub_title'),
                date_text=thumb_params.get('date_text'),
                tag_text=thumb_params.get('tag_text'),
                bg_preset_index=int(thumb_params.get('bg_index', 0)),
                layout=thumb_params.get('layout', 'center'),
                output_filename=f"{base_name}.jpg"
            )
            
            task.result['cover_path'] = final_cover_path
            task.progress = 70
            db.session.commit()

            # --- Step 3: Upload ---
            task.current_step = 'upload'
            db.session.commit()
            
            logger.info(f"Task {task_id}: Uploading to Bilibili...")
            bili_params = params.get('bilibili', {})
            
            config = get_config()
            uploader = BiliUploader(
                sessdata=config.BILI_SESSDATA,
                bili_jct=config.BILI_BILI_JCT,
                buvid3=config.BILI_BUVID3,
                dedeuserid=config.BILI_DEDEUSERID
            )
            
            async def _do_upload():
                return await uploader.upload_single(
                    video_path=final_video_path,
                    title=bili_params.get('title'),
                    desc=bili_params.get('desc', ''),
                    tid=int(bili_params.get('tid', 17)),
                    tags=bili_params.get('tags', []),
                    cover_path=final_cover_path,
                    copyright=int(bili_params.get('copyright', 1)),
                    no_reprint=int(bili_params.get('no_reprint', 1)),
                    dynamic=bili_params.get('dynamic', '')
                )
            
            upload_result = asyncio.run(_do_upload())
            
            task.result['bilibili_result'] = upload_result
            task.status = 'completed'
            task.current_step = 'done'
            task.progress = 100
            task.completed_at = datetime.utcnow()
            db.session.commit()
            
            logger.info(f"Task {task_id} completed successfully.")

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            task.status = 'failed'
            task.error_message = str(e)
            db.session.commit()