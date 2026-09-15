from flask import Blueprint, request, jsonify
from models import db, DownloadTask
from services.runtime_guard import apply_async_after_db_release
from tasks.downloader import download_video_task
import logging
from datetime import datetime

bp = Blueprint('downloader', __name__, url_prefix='/api/v1/downloader')
logger = logging.getLogger(__name__)
DOWNLOAD_TYPES = {'video', 'audio', 'srt', 'text'}

@bp.route('/submit', methods=['POST'])
def submit_download():
    """Submit a URL for download."""
    try:
        data = request.get_json(silent=True) or {}
        url = data.get('url')
        download_type = data.get('download_type', 'video')
        
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'}), 400
        if download_type not in DOWNLOAD_TYPES:
            return jsonify({'success': False, 'error': 'Unsupported download type'}), 400
            
        # Create task record
        task = DownloadTask(url=url, download_type=download_type, status='pending')
        db.session.add(task)
        db.session.commit()
        
        # Trigger Celery task
        celery_task = apply_async_after_db_release(
            download_video_task,
            args=[task.id, url, download_type],
            queue='render',
        )
        
        # Update with Celery ID
        task.celery_task_id = celery_task.id
        db.session.commit()
        
        return jsonify({
            'success': True,
            'task_id': task.id,
            'message': 'Download task submitted successfully'
        })
        
    except Exception as e:
        logger.error(f"Failed to submit download: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/tasks', methods=['GET'])
def get_tasks():
    """Get all download tasks."""
    try:
        tasks = DownloadTask.query.order_by(DownloadTask.created_at.desc()).all()
        return jsonify({
            'success': True,
            'data': [t.to_dict() for t in tasks]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """Get status of a specific task."""
    try:
        task = DownloadTask.query.get(task_id)
        if not task:
            return jsonify({'success': False, 'error': 'Task not found'}), 404
            
        return jsonify({
            'success': True,
            'data': task.to_dict()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/task/<task_id>/cancel', methods=['POST'])
def cancel_task(task_id):
    """Cooperatively cancel a queued or running download."""
    try:
        task = DownloadTask.query.get(task_id)
        if not task:
            return jsonify({'success': False, 'error': 'Task not found'}), 404
        if task.status not in {'pending', 'downloading', 'processing'}:
            return jsonify({'success': False, 'error': 'Task is not cancellable'}), 409
        task.status = 'cancelled'
        task.error_message = None
        db.session.commit()
        return jsonify({'success': True, 'message': 'Download cancelled'})
    except Exception as e:
        logger.error(f"Failed to cancel download: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/task/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    """Delete a task and its record."""
    try:
        task = DownloadTask.query.get(task_id)
        if not task:
            return jsonify({'success': False, 'error': 'Task not found'}), 404
            
        db.session.delete(task)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Task record deleted'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
