from flask import Blueprint, request, jsonify
from models.task import PublicationTask
from models import db
import logging

bp = Blueprint('publisher', __name__, url_prefix='/api/v1/publisher')
logger = logging.getLogger(__name__)

@bp.route('/submit', methods=['POST'])
def submit_task():
    """
    Submit a new publication task.
    """
    # 延迟导入以避免循环依赖
    from tasks.publisher import run_publication_pipeline
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400
            
        # Validation could be added here
        
        # Create DB Task
        task = PublicationTask(params=data)
        db.session.add(task)
        db.session.commit()
        
        # Trigger Celery Task
        celery_task = run_publication_pipeline.delay(task.id)
        
        # Update DB with Celery ID
        task.celery_task_id = celery_task.id
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Task submitted',
            'task_id': task.id,
            'celery_id': celery_task.id
        })

    except Exception as e:
        logger.error(f"Submit task failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/retry/<task_id>', methods=['POST'])
def retry_task(task_id):
    """
    Retry an existing task by creating a new one with same params.
    """
    from tasks.publisher import run_publication_pipeline
    
    try:
        original_task = PublicationTask.query.get(task_id)
        if not original_task:
            return jsonify({'error': 'Task not found'}), 404
            
        # Create new task with same params
        new_task = PublicationTask(params=original_task.params)
        db.session.add(new_task)
        db.session.commit()
        
        # Trigger Celery
        celery_task = run_publication_pipeline.delay(new_task.id)
        
        new_task.celery_task_id = celery_task.id
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Task retried successfully',
            'task_id': new_task.id,
            'celery_id': celery_task.id
        })
        
    except Exception as e:
        logger.error(f"Retry task failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/post-youtube/<task_id>', methods=['POST'])
def post_to_youtube(task_id):
    """
    Directly upload an existing task's video to YouTube.
    """
    from tasks.publisher import run_youtube_upload_only
    
    try:
        task = PublicationTask.query.get(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
            
        if not task.result or not task.result.get('video_path'):
            return jsonify({'error': 'No video file found for this task. It must be completed first.'}), 400
            
        # Trigger Celery
        celery_task = run_youtube_upload_only.delay(task.id)
        
        return jsonify({
            'success': True,
            'message': 'YouTube upload started',
            'task_id': task.id,
            'celery_id': celery_task.id
        })
        
    except Exception as e:
        logger.error(f"Post to YouTube failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """
    Get status of a specific task.
    """
    task = PublicationTask.query.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
        
    return jsonify({
        'success': True,
        'data': task.to_dict()
    })

@bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    List recent tasks (pagination could be added).
    """
    tasks = PublicationTask.query.order_by(PublicationTask.created_at.desc()).limit(20).all()
    return jsonify({
        'success': True,
        'data': [t.to_dict() for t in tasks]
    })
