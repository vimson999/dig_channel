import asyncio
import os
import sys
import logging
import json
import hashlib
import subprocess
from datetime import datetime

# Ensure src/backend is in sys.path for Celery worker
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Get project root (tasks/publisher.py -> backend -> src -> root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(backend_dir))

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
    
    # 1. 如果已经是绝对路径且存在，直接返回
    if os.path.isabs(rel_path) and os.path.exists(rel_path):
        return rel_path
        
    # 2. 优先尝试相对于项目根目录
    abs_root_path = os.path.abspath(os.path.join(PROJECT_ROOT, rel_path))
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

def has_usable_subtitles(value):
    """Return True only when subtitle payload contains real subtitle text."""
    if not value:
        return False
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped and stripped not in {"识别中...", "识别失败"})
    if isinstance(value, dict):
        return has_usable_subtitles(value.get("text"))
    if isinstance(value, list):
        return any(has_usable_subtitles(item) for item in value)
    return False

@celery_app.task(bind=True)
def run_publication_pipeline(self, task_id):
    """
    Execute the full publication pipeline: Synthesis -> Thumbnail -> Upload.
    """
    import os
    import sys
    # 确保后端目录在路径中
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
        
    from app import create_app
    import time
    overall_start = time.time()
    app = create_app()
    
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found in DB")
            return
    
        try:
            overall_start = time.time()
            params = task.params

            # 🚀 [DEBUG LOG] 记录输入原始参数
            logger.info(f"🔍 [TASK] 任务启动参数详情: {json.dumps(params, ensure_ascii=False, indent=2)}")

            vid_params = params.get('video', {})
            v_vid_params = params.get('vertical_video', {})
            bili_params = params.get('bilibili', {})
            thumb_params = params.get('thumbnail', {})

            # 初始化路径变量
            final_video_path = None
            v_video_path = None

            logger.info(f"🚀 [TASK] 开始执行发布流水线 | 任务ID: {task_id}")
            
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
            audio_segments_raw = params.get('audio_segments', [])
            audio_path_abs = []
            subtitles = []

            # 1. 尝试从新页面的“时间轴轨道”获取数据
            if audio_segments_raw:
                logger.info(f"🎞️ [TASK] 模式: 多轨道剪辑 | 片段数: {len(audio_segments_raw)}")
                for i, seg in enumerate(audio_segments_raw):
                    raw_path = to_abs_path(seg.get('path'))
                    if not raw_path or not os.path.exists(raw_path): continue
                    
                    speed = float(seg.get('speed', 1.0))
                    start_offset = float(seg.get('start', 0))
                    
                    # 生成物理剪辑后的临时文件
                    file_dir, file_name = os.path.split(raw_path)
                    name_part, ext_part = os.path.splitext(file_name)
                    edit_path = os.path.join(file_dir, f"{name_part}_edit_s{speed}_st{start_offset}{ext_part}")
                    
                    # 检查临时文件是否需要重新生成（不存在，或原始文件更新）
                    need_rebuild = not os.path.exists(edit_path)
                    if not need_rebuild:
                        if os.path.getmtime(raw_path) > os.path.getmtime(edit_path):
                            logger.info(f"  - 🔄 原始文件已更新，准备重新生成剪辑片段")
                            need_rebuild = True

                    if need_rebuild:
                        cmd = ["ffmpeg", "-y", "-ss", str(start_offset), "-i", raw_path, "-filter:a", f"atempo={speed}", "-vn", edit_path]
                        try:
                            subprocess.run(cmd, check=True, capture_output=True)
                            audio_path_abs.append(edit_path)
                        except: audio_path_abs.append(raw_path)
                    else:
                        audio_path_abs.append(edit_path)
                    
                    subtitles.append(seg.get('subtitle', ''))

            # 2. 如果没传轨道数据，则回退到旧页面的“全局模式”
            if not audio_path_abs:
                logger.info("🎞️ [TASK] 模式: 全局单文件 (兼容旧版)")
                audio_raw_val = vid_params.get('audio_path') or v_vid_params.get('audio_path')
                if audio_raw_val:
                    if isinstance(audio_raw_val, str) and audio_raw_val.strip().startswith('['):
                        try:
                            paths_list = json.loads(audio_raw_val)
                            audio_path_abs = [to_abs_path(p) for p in paths_list]
                        except: audio_path_abs = to_abs_path(audio_raw_val)
                    else: audio_path_abs = to_abs_path(audio_raw_val)
                
                # 兼容旧版字幕存放位置
                subtitles = vid_params.get('subtitles') or v_vid_params.get('subtitles') or []

            # 3. 基础参数初始化
            asr_enabled = vid_params.get('asr_enabled', False)
            sub_burn_enabled = vid_params.get('sub_burn_enabled', False)
            asr_model_size = vid_params.get('asr_model') or params.get('asr_model') or 'base'
            
            logger.info(f"  - 初始配置 | ASR开关: {asr_enabled} | 字幕烧录开关: {sub_burn_enabled} | 模型: {asr_model_size}")
            
            # --- 智能 ASR 补偿逻辑 ---
            # 改进：如果开启了字幕烧录 (sub_burn_enabled) 且当前字幕为空，强制触发 ASR
            should_trigger_asr = asr_enabled or (sub_burn_enabled and not has_usable_subtitles(subtitles))
            
            if audio_path_abs and should_trigger_asr:
                from services.asr_service import asr_service
                logger.info(f"🔄 [TASK] 触发自动 ASR 识别 (模型: {asr_model_size})")
                processed_subs = []
                audio_list = audio_path_abs if isinstance(audio_path_abs, list) else [audio_path_abs]
                
                # 确保 subtitles 列表长度与音频列表一致
                if not subtitles: 
                    subtitles = ["" for _ in audio_list]
                elif not isinstance(subtitles, list):
                    subtitles = [subtitles]
                
                for i, a_p in enumerate(audio_list):
                    s_data = subtitles[i] if i < len(subtitles) else ""
                    if not s_data or s_data in ["识别中...", "识别失败"]:
                        try:
                            # --- ASR 缓存逻辑 ---
                            # 使用文件路径、大小、修改时间以及模型大小生成唯一缓存 Key
                            # 这样可以确保：1. 同文件不同倍速（路径不同）缓存分离 2. 原始文件被替换（mtime/size不同）缓存失效
                            f_stat = os.stat(a_p)
                            cache_payload = f"{a_p}_{f_stat.st_size}_{f_stat.st_mtime}_{asr_model_size}"
                            cache_key = hashlib.md5(cache_payload.encode()).hexdigest()
                            cache_path = os.path.join(PROJECT_ROOT, "data/processed/asr_cache", f"{cache_key}.json")
                            
                            if os.path.exists(cache_path):
                                logger.info(f"  - ⚡️ [CACHE] 命中识别缓存: {os.path.basename(a_p)}")
                                with open(cache_path, 'r', encoding='utf-8') as f:
                                    s_data = json.load(f)
                            else:
                                logger.info(f"  - 🎙️ 正在识别第 {i+1}/{len(audio_list)} 个片段: {os.path.basename(a_p)}")
                                s_data = asr_service.transcribe(a_p, return_segments=True, model_size=asr_model_size)
                                
                                # 确保目录存在
                                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                                # 写入缓存
                                with open(cache_path, 'w', encoding='utf-8') as f:
                                    json.dump(s_data, f, ensure_ascii=False, indent=2)
                                logger.info(f"  - ✅ 识别完成并存入缓存: {cache_key[:8]}")
                            
                        except Exception as e:
                            logger.error(f"❌ [TASK] 第 {i+1} 个片段 ASR 识别失败: {e}")
                            s_data = ""
                    processed_subs.append(s_data)
                subtitles = processed_subs
                logger.info(f"✅ [TASK] ASR 流程完成")
            
            # If sub_burn_enabled is False, we clear subtitles so synthesis methods don't render them
            final_subtitles = subtitles if sub_burn_enabled and has_usable_subtitles(subtitles) else None
            if final_subtitles:
                logger.info(f"📝 [TASK] 最终参与烧录的字幕条数: {len(final_subtitles)}")
            elif sub_burn_enabled:
                logger.warning("⚠️ [TASK] 字幕烧录已开启，但没有可用字幕，跳过字幕烧录")
    
            # --- Step 1: Video Synthesis Routing based on Aspect Ratio ---
            # 新版编辑器通过 aspect_ratio 参数显式指定比例，而将所有参数都放在 video 对象中
            global_aspect_ratio = params.get('aspect_ratio', '16:9')
            is_explicit_vertical = global_aspect_ratio == '9:16'
            
            # 判断逻辑：
            # 如果显式指定了 9:16，则只执行竖屏合成 (将 video 块的参数当做竖屏参数处理)
            # 否则，按照旧版逻辑：如果有 video 块则横屏，如果有 vertical_video 块则竖屏
            
            landscape_enabled = vid_params.get('enabled', False) and not is_explicit_vertical
            vertical_enabled = v_vid_params.get('enabled', False) or (vid_params.get('enabled', False) and is_explicit_vertical)

            if landscape_enabled:
                task.status = 'processing'
                task.current_step = 'synthesis'
                task.progress = 10
                db.session.commit()
                
                video_path = to_abs_path(vid_params.get('video_path'))
                image_path = to_abs_path(vid_params.get('image_path'))
                
                logger.info(f"Task {task_id}: Starting landscape video synthesis...")
                logger.info(f"  - Video: {video_path}")
                logger.info(f"  - Image: {image_path}")
                logger.info(f"  - Audio Segments: {len(audio_path_abs) if isinstance(audio_path_abs, list) else 1}")
                
                video_c_path = to_abs_path(vid_params.get('video_c_path'))
                audio_c_path = to_abs_path(vid_params.get('audio_c_path'))
                audio_start = float(vid_params.get('audio_start', 0))
                audio_end_offset = float(vid_params.get('audio_end_offset', 0))
                cta_config = vid_params.get('cta')
                transform = vid_params.get('transform')
                subtitle_position = vid_params.get('subtitle_position')
                subtitle_size = vid_params.get('subtitle_size', 'normal')
                export_params = params.get('export_params')

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
                    subtitles=final_subtitles,
                    transform=transform,
                    subtitle_position=subtitle_position,
                    subtitle_size=subtitle_size,
                    export_params=export_params
                )
                logger.info(f"  - ✅ Landscape Done: {final_video_path}")
                task.result = {**(task.result or {}), 'video_path': final_video_path}

            # --- Step 1.5: Vertical Synthesis ---
            if vertical_enabled:
                task.current_step = 'v_synthesis'
                task.progress = 30
                db.session.commit()

                # 如果是显式指定竖屏，则参数从 vid_params 取；否则从 v_vid_params 取
                active_v_params = vid_params if is_explicit_vertical else v_vid_params
                
                v_image_path = to_abs_path(active_v_params.get('image_path'))
                if not v_image_path or not os.path.exists(v_image_path):
                    # Fallback
                    v_image_path = to_abs_path(vid_params.get('image_path'))

                logger.info(f"Task {task_id}: Starting vertical video synthesis...")
                logger.info(f"  - Image: {v_image_path}")
                logger.info(f"  - Audio Segments: {len(audio_path_abs) if isinstance(audio_path_abs, list) else 1}")

                v_subtitle_position = active_v_params.get('subtitle_position')
                v_subtitle_size = active_v_params.get('subtitle_size', 'normal')
                v_export_params = params.get('export_params')

                v_video_path = video_editor_service.process_vertical_video(
                    image_file=v_image_path,
                    audio_files=audio_path_abs,
                    subtitles=final_subtitles,
                    output_filename=f"v_{base_name}.mp4",
                    subtitle_position=v_subtitle_position,
                    subtitle_size=v_subtitle_size,
                    export_params=v_export_params
                )
                logger.info(f"  - ✅ Vertical Done: {v_video_path}")
                
                # 如果这是唯一的合成任务，把它也放到主 video_path 里方便前端下载
                res_dict = {**(task.result or {}), 'vertical_video_path': v_video_path}
                if is_explicit_vertical:
                    res_dict['video_path'] = v_video_path
                    
                task.result = res_dict
            
            # --- Step 1.6: Alternative Vertical Synthesis (Vertical Video 2) ---
            v_vid_2_params = params.get('vertical_video_2', {})
            if v_vid_2_params.get('enabled', False):
                task.current_step = 'v_synthesis_2'
                db.session.commit()
                
                logger.info(f"Task {task_id}: Starting alternative vertical video synthesis...")
                
                v2_image_path = to_abs_path(v_vid_2_params.get('image_path'))
                if v2_image_path and os.path.exists(v2_image_path):
                    v2_video_path = video_editor_service.process_vertical_video(
                        image_file=v2_image_path,
                        audio_files=audio_path_abs,
                        subtitles=final_subtitles,
                        output_filename=f"v2_{base_name}.mp4",
                        margin_v_override=200
                    )
                    task.result = {**(task.result or {}), 'vertical_video_2_path': v2_video_path}
                else:
                    logger.warning(f"Task {task_id}: Skipping vertical_video_2, image path missing or invalid.")
            
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
                    overlay_opacity=float(thumb_params.get('overlay_opacity', 0.5)),
                    transform=thumb_params.get('transform')
                )
                
                task.result = {**(task.result or {}), 'cover_path': final_cover_path}
                task.progress = 70
                db.session.commit()
            else:
                task.progress = 70
                db.session.commit()
    
            # --- Step 3: Upload ---
            if bili_params.get('enabled', False):
                task.current_step = 'upload'
                db.session.commit()
                
                # 智能选择上传文件：如果没开横屏开了竖屏，就传竖屏；否则传横屏
                upload_path = final_video_path
                if not vid_params.get('enabled', True) and v_vid_params.get('enabled', False):
                    upload_path = v_video_path
                
                logger.info(f"Task {task_id}: Uploading to Bilibili... [Path: {upload_path}]")
                if not upload_path or not os.path.exists(upload_path):
                    logger.error(f"❌ [UPLOAD] 找不到可上传的视频文件: {upload_path}")
                    raise FileNotFoundError(f"找不到上传视频: {upload_path}")

                # 从数据库读取 B 站凭证
                uploader = BiliUploader(
                    sessdata=ConfigService.get('sessdata', 'bilibili_auth'),
                    bili_jct=ConfigService.get('bili_jct', 'bilibili_auth'),
                    buvid3=ConfigService.get('buvid3', 'bilibili_auth'),
                    dedeuserid=ConfigService.get('dedeuserid', 'bilibili_auth')
                )
                
                async def _do_upload():
                    return await uploader.upload_single(
                        video_path=upload_path,
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
                logger.info(f"Task {task_id}: Skipping Bilibili upload (disabled or not enabled).")
                task.progress = 90
                db.session.commit()
    
            # --- Step 4: YouTube Upload ---
            yt_params = params.get('youtube', {})
            if yt_params and yt_params.get('enabled', False):
                task.current_step = 'upload_youtube'
                db.session.commit()
                
                # 智能选择上传文件
                upload_path_yt = final_video_path
                if not vid_params.get('enabled', True) and v_vid_params.get('enabled', False):
                    upload_path_yt = v_video_path

                logger.info(f"Task {task_id}: Uploading to YouTube... [Path: {upload_path_yt}]")
                try:
                    yt_uploader = YouTubeUploader()
                    yt_response = yt_uploader.upload_video(
                        video_path=upload_path_yt,
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
            
            total_duration = time.time() - overall_start
            logger.info(f"✨ [TASK] 任务 {task_id} 全部处理完成! 总耗时: {total_duration:.2f}s")
    
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
