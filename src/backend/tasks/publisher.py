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
from services.youtube.uploader import YouTubeUploader
from services.config_service import ConfigService
from config import get_config

logger = logging.getLogger(__name__)

def to_abs_path(rel_path):
    """Helper to convert relative path to project-root-based absolute path."""
    if not rel_path: return None
    if os.path.isabs(rel_path): return rel_path
    # Root is 4 levels up: tasks/publisher.py -> backend -> src -> root
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base_dir, rel_path)

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
            # --- Step 1: Synthesis ---
            task.status = 'processing'
            task.current_step = 'synthesis'
            task.progress = 10
            db.session.commit()
            
            logger.info(f"Task {task_id}: Starting video synthesis...")
            params = task.params
            
            # Extract and Absolute-ify Video Params
            vid_params = params.get('video', {})
            video_path = to_abs_path(vid_params.get('video_path'))
            image_path = to_abs_path(vid_params.get('image_path'))
            audio_path = to_abs_path(vid_params.get('audio_path'))
            video_c_path = to_abs_path(vid_params.get('video_c_path'))
            audio_c_path = to_abs_path(vid_params.get('audio_c_path'))
            
            audio_start = float(vid_params.get('audio_start', 7.0))
            audio_end_offset = float(vid_params.get('audio_end_offset', 0.0))
            
            # Synthesis
            final_video_path = video_editor_service.process_mixed_content(
                video_file=video_path,
                image_file=image_path,
                audio_file=audio_path,
                video_c_path=video_c_path,
                audio_c_path=audio_c_path,
                audio_start=audio_start,
                audio_end_offset=audio_end_offset,
                output_filename=f"pub_{task_id}.mp4"
            )
            
            task.result = task.result or {}
            task.result['video_path'] = final_video_path
            task.progress = 50
            db.session.commit()

            # --- Step 2: Thumbnail ---
            task.current_step = 'thumbnail'
            db.session.commit()
            
            logger.info(f"Task {task_id}: Generating thumbnail...")
            thumb_params = params.get('thumbnail', {})
            
            final_cover_path = thumbnail_service.generate_thumbnail(
                main_title=thumb_params.get('main_title'),
                sub_title=thumb_params.get('sub_title'),
                date_text=thumb_params.get('date_text'),
                tag_text=thumb_params.get('tag_text'),
                bg_preset_index=int(thumb_params.get('bg_index', 0)),
                layout=thumb_params.get('layout', 'center'),
                output_filename=f"pub_cover_{task_id}.jpg"
            )
            
            task.result['cover_path'] = final_cover_path
            task.progress = 70
            db.session.commit()

            # --- Step 3: Upload ---
            task.current_step = 'upload'
            db.session.commit()
            
            logger.info(f"Task {task_id}: Uploading to Bilibili...")
            bili_params = params.get('bilibili', {})
            
            # 从数据库读取 B 站凭证
            uploader = BiliUploader(
                sessdata=ConfigService.get('sessdata', 'bilibili_auth'),
                bili_jct=ConfigService.get('bili_jct', 'bilibili_auth'),
                buvid3=ConfigService.get('buvid3', 'bilibili_auth'),
                dedeuserid=ConfigService.get('dedeuserid', 'bilibili_auth')
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
            db.session.commit()

            # --- Step 4: YouTube Upload ---
            yt_params = params.get('youtube', {})
            if yt_params:
                task.current_step = 'upload_youtube'
                db.session.commit()
                
                logger.info(f"Task {task_id}: Uploading to YouTube...")
                try:
                    yt_uploader = YouTubeUploader()
                    yt_response = yt_uploader.upload_video(
                        video_path=final_video_path,
                        title=yt_params.get('title', bili_params.get('title')), # Fallback to Bili title
                        description=yt_params.get('desc', bili_params.get('desc', '')),
                        tags=yt_params.get('tags', bili_params.get('tags', [])),
                        category_id=yt_params.get('category_id', '22'),
                        privacy_status=yt_params.get('privacy', 'private'),
                        thumbnail_path=final_cover_path
                    )
                    task.result['youtube_result'] = yt_response
                except Exception as ye:
                    logger.error(f"YouTube upload failed for task {task_id}: {ye}")
                    task.result['youtube_error'] = str(ye)
                
                db.session.commit()

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