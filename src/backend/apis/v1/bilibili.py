import os
import asyncio
import logging
import threading
from pathlib import Path
from flask import Blueprint, request, jsonify, send_from_directory, current_app
from services.bilibili_sync_service import BilibiliSyncService
from services.bilibili import load_metadata, save_metadata, BiliVideoMetadata
from bilibili_api import user, Credential
from werkzeug.utils import secure_filename

bp = Blueprint('bilibili', __name__, url_prefix='/api/v1/bilibili')
logger = logging.getLogger("bilibili")

# Initialize service globally or lazily? Lazily is better for config, but we need a singleton-like access.
# For simplicity, we create a new instance when needed or cache it if config is static.
# But config might rely on app context.

def get_bili_service():
    """Helper to get service instance with current app config."""
    # Note: BilibiliSyncService creates its own BiliUploader which reads config.
    # Ensure config keys are passed correctly.
    # We can rely on BaseConfig being loaded in app.config, but BilibiliSyncService 
    # constructor expects a config object or uses BaseConfig defaults.
    # Let's pass the current app.config object if it mimics BaseConfig structure, 
    # or let it use BaseConfig if env vars are set.
    return BilibiliSyncService()

# Helper for async execution in Flask
def run_async(coro):
    """Run an async coroutine in a new event loop (synchronously waiting)."""
    try:
        return asyncio.run(coro)
    except Exception as e:
        logger.error(f"Async execution error: {e}")
        raise e

@bp.route('/status', methods=['GET'])
def get_status():
    """Check Bilibili login status and get user info."""
    service = get_bili_service()
    
    async def _check():
        try:
            # Check if credential has enough info
            cred = service.uploader.credential
            logger.info(f"Checking Bili Credentials: SESSDATA={'Set' if cred.sessdata else 'Missing'}, JCT={'Set' if cred.bili_jct else 'Missing'}")
            
            if not (cred.sessdata and cred.bili_jct):
                 return {'configured': False, 'message': 'Missing credentials in environment'}
            
            # Fetch user info
            u = user.User(credential=cred, uid=int(os.environ.get('BILI_DEDEUSERID', 0)))
            # If UID is not set, we might need to fetch 'get_self_info' but User requires uid?
            # actually user.get_self_info() is a method on User class but needs an instance or check API.
            # 'get_self_info' is a static/class method or requires an instance? 
            # In bilibili-api 17.x:
            # data = await user.get_self_info(credential)
            data = await user.get_self_info(cred)
            return {
                'configured': True,
                'valid': True,
                'data': {
                    'name': data.get('name'),
                    'face': data.get('face'),
                    'level': data.get('level_info', {}).get('current_level'),
                    'mid': data.get('mid')
                }
            }
        except Exception as e:
            logger.error(f"Status check failed: {e}")
            return {'configured': True, 'valid': False, 'message': str(e)}

    try:
        result = run_async(_check())
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/queue', methods=['GET'])
def get_queue():
    """List videos in the input queue."""
    service = get_bili_service()
    files = []
    
    # List supported files in input_dir
    if service.input_dir.exists():
        for p in service.input_dir.iterdir():
            if p.is_file() and p.suffix.lower() in service.SUPPORTED_EXTENSIONS:
                meta = load_metadata(p)
                files.append({
                    'filename': p.name,
                    'title': meta.title,
                    'desc': meta.desc,
                    'tid': meta.tid,
                    'tags': meta.tags,
                    'cover_path': meta.cover_path,
                    'size': p.stat().st_size,
                    'modified': p.stat().st_mtime
                })
    
    # Sort by modification time by default
    files.sort(key=lambda x: x['modified'])
    
    return jsonify({'success': True, 'data': files})

@bp.route('/history', methods=['GET'])
def get_history():
    """List recently processed videos (success and failed)."""
    service = get_bili_service()
    history = []
    
    # Helper to scan dir
    def scan_dir(path, status):
        if path.exists():
            for p in path.iterdir():
                if p.is_file() and p.suffix.lower() in service.SUPPORTED_EXTENSIONS:
                    # Try to load meta, might not exist if moved purely
                    meta = load_metadata(p)
                    history.append({
                        'filename': p.name,
                        'title': meta.title or p.name,
                        'status': status,
                        'modified': p.stat().st_mtime,
                        'size': p.stat().st_size
                    })

    scan_dir(service.processed_dir, 'success')
    scan_dir(service.failed_dir, 'failed')
    
    # Sort by recent first
    history.sort(key=lambda x: x['modified'], reverse=True)
    
    # Limit to top 20
    return jsonify({'success': True, 'data': history[:20]})

@bp.route('/task', methods=['POST'])
def add_task():
    """Upload a new video to the queue."""
    service = get_bili_service()
    
    if 'video' not in request.files:
        return jsonify({'success': False, 'message': 'No video file provided'}), 400
        
    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'success': False, 'message': 'No selected file'}), 400
        
    filename = secure_filename(video_file.filename)
    save_path = service.input_dir / filename
    
    try:
        video_file.save(str(save_path))
        
        # Handle metadata
        title = request.form.get('title', Path(filename).stem)
        desc = request.form.get('desc', '')
        tid = int(request.form.get('tid', 171))
        tags_str = request.form.get('tags', 'BiliFlow')
        tags = [t.strip() for t in tags_str.split(',') if t.strip()]
        copyright_val = int(request.form.get('copyright', 1))
        dynamic = request.form.get('dynamic', '')
        
        # Handle cover
        cover_path = None
        if 'cover' in request.files:
            cover_file = request.files['cover']
            if cover_file.filename:
                cover_name = Path(filename).stem + "_cover" + Path(cover_file.filename).suffix
                cover_save_path = service.input_dir / cover_name
                cover_file.save(str(cover_save_path))
                cover_path = str(cover_save_path.absolute())
        
        # Save metadata
        meta = BiliVideoMetadata(
            title=title,
            desc=desc,
            tid=tid,
            tags=tags,
            cover_path=cover_path,
            copyright=copyright_val,
            dynamic=dynamic
        )
        save_metadata(save_path, meta)
        
        return jsonify({'success': True, 'message': 'Task added successfully'})
        
    except Exception as e:
        logger.error(f"Add task failed: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/task/<filename>', methods=['PUT'])
def update_task(filename):
    """Update metadata for an existing task."""
    service = get_bili_service()
    video_path = service.input_dir / filename
    
    if not video_path.exists():
        return jsonify({'success': False, 'message': 'File not found'}), 404
        
    try:
        data = request.json
        meta = load_metadata(video_path)
        
        # Update fields
        if 'title' in data: meta.title = data['title']
        if 'desc' in data: meta.desc = data['desc']
        if 'tid' in data: meta.tid = int(data['tid'])
        if 'tags' in data: 
            if isinstance(data['tags'], list):
                meta.tags = data['tags']
            else:
                meta.tags = [t.strip() for t in str(data['tags']).split(',') if t.strip()]
        if 'copyright' in data: meta.copyright = int(data['copyright'])
        if 'dynamic' in data: meta.dynamic = data['dynamic']
        
        save_metadata(video_path, meta)
        return jsonify({'success': True, 'message': 'Task updated'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/task/<filename>', methods=['DELETE'])
def delete_task(filename):
    """Delete a task and its related files."""
    service = get_bili_service()
    video_path = service.input_dir / filename
    
    if not video_path.exists():
        return jsonify({'success': False, 'message': 'File not found'}), 404
        
    try:
        # Delete video
        video_path.unlink()
        
        # Delete metadata and cover
        meta_path = video_path.with_suffix(video_path.suffix + ".json")
        if meta_path.exists():
            meta_path.unlink()
            
        # Try to find associated cover image if it follows naming convention or read from metadata before deleting
        # Simple cleanup: check common extensions
        stem = video_path.stem
        for ext in ['.jpg', '.png', '.jpeg']:
            cover = service.input_dir / f"{stem}_cover{ext}"
            if cover.exists():
                cover.unlink()
            # Also check generated cover
            gen_cover = video_path.with_suffix(".jpg")
            if gen_cover.exists():
                gen_cover.unlink()

        return jsonify({'success': True, 'message': 'Task deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/batch/start', methods=['POST'])
def start_batch():
    """Start the batch upload process in a background thread."""
    service = get_bili_service()
    
    def background_task():
        async def _run():
            logger.info("Batch upload started manually via API.")
            await service.run_batch()
            
        try:
            asyncio.run(_run())
        except Exception as e:
            logger.error(f"Background batch failed: {e}")

    thread = threading.Thread(target=background_task)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': 'Batch process started in background'})

@bp.route('/logs', methods=['GET'])
def get_logs():
    """Get the latest logs."""
    try:
        lines = int(request.args.get('lines', 50))
        log_file = Path(current_app.config['LOG_DIR']) / 'bilibili.log'
        
        if not log_file.exists():
            return jsonify({'success': True, 'data': []})
            
        # Simple tail implementation
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            return jsonify({'success': True, 'data': all_lines[-lines:]})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
