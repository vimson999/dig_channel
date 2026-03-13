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
    """Helper to convert path to absolute, staying robust regardless of current working directory."""
    if not rel_path: return None
    
    # Get project root (tasks/publisher.py -> backend -> src -> root)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    
    # 1. 如果已经是绝对路径且存在，直接返回
    if os.path.isabs(rel_path) and os.path.exists(rel_path):
        return rel_path
        
    # 2. 优先尝试相对于项目根目录
    abs_root_path = os.path.abspath(os.path.join(project_root, rel_path))
    if os.path.exists(abs_root_path):
        return abs_root_path
        
    # 3. 备选方案：尝试相对于当前工作目录
    cwd_path = os.path.abspath(os.path.join(os.getcwd(), rel_path))
    if os.path.exists(cwd_path):
        return cwd_path

    # 如果都找不到，返回基于项目根目录的路径作为默认期望值
    return abs_root_path

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

            # --- Step 0: Pre-process Audio & Subtitles (Shared for H and V) ---
            # Use general audio source from main video params as foundation
            vid_params = params.get('video', {})
            audio_raw_val = vid_params.get('audio_path')
            audio_path_abs = None
            if audio_raw_val:
                import json
                if isinstance(audio_raw_val, str) and audio_raw_val.strip().startswith('['):
                    try:
                        paths_list = json.loads(audio_raw_val)
                        audio_path_abs = [to_abs_path(p) for p in paths_list]
                    except: audio_path_abs = to_abs_path(audio_raw_val)
                else: audio_path_abs = to_abs_path(audio_raw_val)

            # Get subtitles (UI source of truth)
            v_vid_params = params.get('vertical_video', {})
            subtitles = v_vid_params.get('subtitles')
            
            asr_enabled = vid_params.get('asr_enabled', False)
            sub_burn_enabled = vid_params.get('sub_burn_enabled', False)

            # Backend ASR Compensation (Unified) - Only if ASR is enabled
            if audio_path_abs and asr_enabled:
                from services.asr_service import asr_service
                processed_subs = []
                audio_list = audio_path_abs if isinstance(audio_path_abs, list) else [audio_path_abs]
                if not subtitles: subtitles = ["" for _ in audio_list]
                
                for i, a_p in enumerate(audio_list):
                    s_text = subtitles[i] if i < len(subtitles) else ""
                    if not s_text or s_text == "识别中...":
                        try:
                            logger.info(f"Task {task_id}: Shared ASR triggered for segment {i}")
                            s_text = asr_service.transcribe(a_p)
                        except Exception as e:
                            logger.error(f"Shared ASR failed: {e}")
                            s_text = ""
                    processed_subs.append(s_text)
                subtitles = processed_subs
            
            # If sub_burn_enabled is False, we clear subtitles so synthesis methods don't render them
            final_subtitles = subtitles if sub_burn_enabled else None

            # --- Step 1: Landscape Synthesis ---
            landscape_enabled = vid_params.get('enabled', True)
            if landscape_enabled:
                task.status = 'processing'
                task.current_step = 'synthesis'
                task.progress = 10
                db.session.commit()
                
                logger.info(f"Task {task_id}: Starting landscape video synthesis...")
                
                video_path = to_abs_path(vid_params.get('video_path'))
                image_path = to_abs_path(vid_params.get('image_path'))
                video_c_path = to_abs_path(vid_params.get('video_c_path'))
                audio_c_path = to_abs_path(vid_params.get('audio_c_path'))
                audio_start = float(vid_params.get('audio_start', 0))
                audio_end_offset = float(vid_params.get('audio_end_offset', 0))
                cta_config = vid_params.get('cta')
                
                final_video_path = video_editor_service.process_mixed_content(
                    video_file=video_path,
                    image_file=image_path,
                    audio_file=audio_path_abs,
                    video_c_path=video_c_path,
                    audio_c_path=audio_c_path,
                    audio_start=audio_start,
                    audio_end_offset=audio_end_offset,
                    output_filename=f"{base_name}.mp4",
                    cta_config=cta_config,
                    subtitles=final_subtitles
                )
                task.result = {**(task.result or {}), 'video_path': final_video_path}
            
            # Define image_path for fallback even if landscape disabled
            if not landscape_enabled:
                image_path = to_abs_path(vid_params.get('image_path'))

            # --- Step 1.5: Vertical Synthesis ---
            if v_vid_params.get('enabled', False):
                task.current_step = 'v_synthesis'
                task.progress = 30
                db.session.commit()
                
                logger.info(f"Task {task_id}: Starting vertical video synthesis...")
                
                v_image_path = to_abs_path(v_vid_params.get('image_path'))
                if not v_image_path or not os.path.exists(v_image_path):
                    v_image_path = to_abs_path(vid_params.get('image_path'))
                
                v_video_path = video_editor_service.process_vertical_video(
                    image_file=v_image_path,
                    audio_files=audio_path_abs,
                    subtitles=final_subtitles,
                    output_filename=f"v_{base_name}.mp4"
                )
                task.result = {**(task.result or {}), 'vertical_video_path': v_video_path}
            
            task.progress = 50
            db.session.commit()

            # --- Step 2: Thumbnail ---
            final_cover_path = None
            if thumb_params:
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
                    output_filename=f"{base_name}.jpg",
                    custom_bg_path=to_abs_path(thumb_params.get('custom_bg_path')),
                    overlay_opacity=float(thumb_params.get('overlay_opacity', 0.5))
                )
                
                task.result = {**(task.result or {}), 'cover_path': final_cover_path}
                task.progress = 70
                db.session.commit()
            else:
                task.progress = 70
                db.session.commit()

            # --- Step 3: Upload ---
            if bili_params.get('enabled', True):
                task.current_step = 'upload'
                db.session.commit()
                
                logger.info(f"Task {task_id}: Uploading to Bilibili...")
                
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
                
                task.result = {**(task.result or {}), 'bilibili_result': upload_result}
                db.session.commit()
            else:
                logger.info(f"Task {task_id}: Skipping Bilibili upload (disabled by user).")
                task.progress = 90
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
                    task.result = {**(task.result or {}), 'youtube_result': yt_response}
                except Exception as ye:
                    logger.error(f"YouTube upload failed for task {task_id}: {ye}", exc_info=True)
                    task.result = {**(task.result or {}), 'youtube_error': str(ye)}
                
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

@celery_app.task(bind=True)
def run_youtube_upload_only(self, task_id):
    """
    Supplements an existing task by uploading its already-synthesized video to YouTube.
    """
    from app import create_app
    app = create_app()
    
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return

        # Check if we have the necessary files
        video_path = task.result.get('video_path')
        if not video_path or not os.path.exists(video_path):
            logger.error(f"Cannot supplement YouTube: Video file missing at {video_path}")
            task.error_message = "无法补发：找不到原始视频文件"
            db.session.commit()
            return

        try:
            task.status = 'processing'
            task.current_step = 'upload_youtube'
            db.session.commit()

            params = task.params
            yt_params = params.get('youtube') or {}
            bili_params = params.get('bilibili') or {}
            final_cover_path = task.result.get('cover_path')

            logger.info(f"Task {task_id}: Supplementing YouTube upload...")
            
            yt_uploader = YouTubeUploader()
            yt_response = yt_uploader.upload_video(
                video_path=video_path,
                title=yt_params.get('title') or bili_params.get('title'),
                description=yt_params.get('desc') or bili_params.get('desc', ''),
                tags=yt_params.get('tags') or bili_params.get('tags', []),
                category_id=yt_params.get('category_id', '22'),
                privacy_status=yt_params.get('privacy', 'private'),
                thumbnail_path=final_cover_path
            )
            
            task.result = {**(task.result or {}), 'youtube_result': yt_response}
            task.status = 'completed'
            task.current_step = 'done'
            task.progress = 100
            db.session.commit()
            logger.info(f"Task {task_id}: YouTube supplement successful.")

        except Exception as e:
            logger.error(f"YouTube supplement failed for task {task_id}: {e}", exc_info=True)
            # Don't mark the whole task as failed if it was previously completed, 
            # just record the error in result
            task.result = {**(task.result or {}), 'youtube_error': str(e)}
            task.status = 'completed' # Keep as completed since Bili was done
            task.current_step = 'done'
            db.session.commit()