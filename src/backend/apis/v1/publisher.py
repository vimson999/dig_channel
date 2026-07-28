from flask import Blueprint, request, jsonify
from models.task import PublicationTask
from models import db
from services.runtime_guard import apply_async_after_db_release
from sqlalchemy import or_
from sqlalchemy.orm import load_only
from datetime import datetime, timedelta
from copy import deepcopy
import base64
import json
import logging
import os
import redis
import threading
import time

bp = Blueprint('publisher', __name__, url_prefix='/api/v1/publisher')
logger = logging.getLogger(__name__)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
PUBLISHER_TASK_LIST_CACHE_TTL_SECONDS = float(os.environ.get('PUBLISHER_TASK_LIST_CACHE_TTL', '10'))
PUBLISHER_TASK_LIST_CACHE = {}
PUBLISHER_TASK_LIST_CACHE_LOCK = threading.Lock()
PUBLISHER_TASK_PAYLOAD_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)


def _get_publication_task(task_id, *columns):
    selected = columns or PUBLISHER_TASK_PAYLOAD_COLUMNS
    return (
        PublicationTask.query.options(load_only(*selected))
        .filter(PublicationTask.id == str(task_id))
        .first()
    )


def _clear_publisher_task_list_cache():
    with PUBLISHER_TASK_LIST_CACHE_LOCK:
        PUBLISHER_TASK_LIST_CACHE.clear()


def _get_publisher_task_list_cache(cache_key):
    if PUBLISHER_TASK_LIST_CACHE_TTL_SECONDS <= 0:
        return None
    with PUBLISHER_TASK_LIST_CACHE_LOCK:
        cached = PUBLISHER_TASK_LIST_CACHE.get(cache_key)
        if not cached or cached.get('expires_at', 0) <= time.time():
            return None
        return deepcopy(cached.get('payload'))


def _set_publisher_task_list_cache(cache_key, payload):
    if PUBLISHER_TASK_LIST_CACHE_TTL_SECONDS <= 0:
        return
    with PUBLISHER_TASK_LIST_CACHE_LOCK:
        PUBLISHER_TASK_LIST_CACHE[cache_key] = {
            'expires_at': time.time() + PUBLISHER_TASK_LIST_CACHE_TTL_SECONDS,
            'payload': deepcopy(payload),
        }


def _is_publisher_task(task):
    params = task.params if isinstance(task.params, dict) else {}
    feature = task.feature or params.get('feature')
    return feature in (None, 'publisher')


def _compact_title(*candidates):
    for candidate in candidates:
        value = str(candidate or '').replace('\r', '\n').strip()
        if value:
            return ' | '.join(part.strip() for part in value.split('\n') if part.strip()) or value
    return '未命名任务'


def _publication_task_summary(row):
    feature = getattr(row, 'feature', None)
    title = _compact_title(
        getattr(row, 'task_title', None),
    )
    is_video_task = feature in (None, '', 'publisher')
    video_path = getattr(row, 'video_path', None)
    result_summary = {'video_path': video_path} if video_path else {}
    return {
        'id': row.id,
        'title': title,
        'celery_task_id': row.celery_task_id,
        'status': row.status,
        'current_step': row.current_step,
        'progress': row.progress or 0,
        'feature': feature or 'publisher',
        'is_video_editor_task': is_video_task,
        'has_reusable_config': is_video_task,
        'params': {'feature': feature or 'publisher', '_summary': True},
        'result': result_summary,
        'error_message': row.error_message,
        'created_at': (row.created_at.isoformat() + "Z") if row.created_at else None,
        'updated_at': (row.updated_at.isoformat() + "Z") if row.updated_at else None,
        'completed_at': (row.completed_at.isoformat() + "Z") if row.completed_at else None,
    }


def _abs_project_path(path):
    if not path:
        return None
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(PROJECT_ROOT, str(path)))


def _expand_path_value(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith('['):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if item]
            except Exception:
                pass
        return [stripped]
    return []


def _collect_audio_paths(params):
    paths = []
    audio_segments = params.get('audio_segments')
    if isinstance(audio_segments, list):
        for segment in audio_segments:
            if isinstance(segment, dict) and segment.get('path'):
                paths.append(str(segment.get('path')))

    for key in ('video', 'vertical_video', 'vertical_video_2'):
        block = params.get(key)
        if isinstance(block, dict):
            paths.extend(_expand_path_value(block.get('audio_path')))

    seen = set()
    unique = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _validate_publication_assets(task):
    params = task.params if isinstance(task.params, dict) else {}
    has_video_work = any(
        isinstance(params.get(key), dict) and params[key].get('enabled')
        for key in ('video', 'vertical_video', 'vertical_video_2')
    )
    if not has_video_work:
        return None

    audio_paths = _collect_audio_paths(params)
    if not audio_paths:
        return '任务缺少音频配置，无法入队'

    existing = [path for path in audio_paths if os.path.exists(_abs_project_path(path))]
    if not existing:
        sample = audio_paths[0]
        return f'任务音频文件不存在，无法入队：{sample}'

    return None


def _enqueue_publication_task(task):
    from tasks.publisher import run_publication_pipeline

    asset_error = _validate_publication_assets(task)
    if asset_error:
        task.status = 'failed'
        task.current_step = 'failed'
        task.progress = 0
        task.error_message = asset_error
        task.completed_at = datetime.utcnow()
        db.session.commit()
        _clear_publisher_task_list_cache()
        raise ValueError(asset_error)

    try:
        celery_task = apply_async_after_db_release(
            run_publication_pipeline,
            args=[task.id],
            queue='render',
            retry=False,
        )
    except Exception as exc:
        logger.exception("Publication task enqueue failed: %s", task.id)
        task.status = 'failed'
        task.current_step = 'failed'
        task.progress = 0
        task.error_message = f'视频任务入队失败: {exc}'
        db.session.commit()
        _clear_publisher_task_list_cache()
        raise

    task.celery_task_id = celery_task.id
    task.status = 'pending'
    task.current_step = 'init'
    task.progress = 0
    task.error_message = None
    task.completed_at = None
    db.session.commit()
    _clear_publisher_task_list_cache()
    return celery_task


def _broker_url():
    return os.environ.get('CELERY_BROKER_URL') or os.environ.get('REDIS_URL') or 'redis://localhost:6379/0'


def _read_render_queue_refs(max_messages=1000):
    """Read lightweight task refs from the Redis render queue.

    Celery's Redis transport stores JSON envelopes. The DB publication task id
    is the first positional argument in our publisher task messages.
    """
    refs = {
        'queue_length': 0,
        'celery_ids': set(),
        'publication_task_ids': set(),
    }
    client = redis.Redis.from_url(
        _broker_url(),
        socket_connect_timeout=2,
        socket_timeout=3,
        retry_on_timeout=False,
    )
    raw_messages = client.lrange('render', 0, max(0, int(max_messages)) - 1)
    refs['queue_length'] = int(client.llen('render'))

    for raw in raw_messages:
        try:
            envelope = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
        except Exception:
            continue

        headers = envelope.get('headers') if isinstance(envelope, dict) else None
        if isinstance(headers, dict) and headers.get('id'):
            refs['celery_ids'].add(str(headers['id']))

        body = envelope.get('body') if isinstance(envelope, dict) else None
        if not isinstance(body, str):
            continue

        try:
            decoded = base64.b64decode(body)
            payload = json.loads(decoded.decode('utf-8'))
        except Exception:
            continue

        args = []
        if isinstance(payload, list) and payload:
            args = payload[0] if isinstance(payload[0], list) else []
        elif isinstance(payload, dict):
            args = payload.get('args') or []

        if args:
            refs['publication_task_ids'].add(str(args[0]))

    return refs


@bp.route('/submit', methods=['POST'])
def submit_task():
    """
    Submit a new publication task.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400
            
        # Validation could be added here
        
        # Create DB Task
        task = PublicationTask(params=data)
        db.session.add(task)
        db.session.commit()

        try:
            celery_task = _enqueue_publication_task(task)
        except Exception:
            return jsonify({'error': task.error_message}), 502
        
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
    try:
        original_task = _get_publication_task(
            task_id,
            PublicationTask.id,
            PublicationTask.params,
            PublicationTask.feature,
        )
        if not original_task:
            return jsonify({'error': 'Task not found'}), 404
        if not _is_publisher_task(original_task):
            return jsonify({'error': 'Only publisher/video tasks can be retried here'}), 400
            
        # Create new task with same params
        new_task = PublicationTask(params=original_task.params)
        db.session.add(new_task)
        db.session.commit()

        try:
            celery_task = _enqueue_publication_task(new_task)
        except Exception:
            return jsonify({'error': new_task.error_message}), 502
        
        return jsonify({
            'success': True,
            'message': 'Task retried successfully',
            'task_id': new_task.id,
            'celery_id': celery_task.id
        })
        
    except Exception as e:
        logger.error(f"Retry task failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/requeue/<task_id>', methods=['POST'])
def requeue_task(task_id):
    """
    Requeue the same publisher/video task in place.
    Useful for tasks stuck in pending because the original Celery publish failed.
    """
    try:
        task = _get_publication_task(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
        if not _is_publisher_task(task):
            return jsonify({'error': 'Only publisher/video tasks can be requeued here'}), 400

        task.result = {}
        task.status = 'pending'
        task.current_step = 'init'
        task.progress = 0
        task.error_message = None
        task.completed_at = None
        db.session.commit()

        try:
            celery_task = _enqueue_publication_task(task)
        except Exception:
            return jsonify({'error': task.error_message}), 502

        return jsonify({
            'success': True,
            'message': 'Task requeued successfully',
            'task_id': task.id,
            'celery_id': celery_task.id
        })

    except Exception as e:
        logger.error(f"Requeue task failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/recover-pending', methods=['POST'])
def recover_pending_tasks():
    """
    Recover stale publisher tasks without duplicating jobs already in Redis.
    """
    try:
        from services.publisher_queue_repair import repair_stale_publisher_processing_tasks_once

        data = request.get_json(silent=True) or {}
        max_age_minutes = float(data.get('max_age_minutes', 2))
        limit = min(max(int(data.get('limit', 10)), 1), 50)
        cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)

        queue_refs = _read_render_queue_refs()

        candidates = (
            PublicationTask.query
            .filter(PublicationTask.feature == 'publisher')
            .filter(PublicationTask.status == 'pending')
            .filter(PublicationTask.updated_at <= cutoff)
            .order_by(PublicationTask.updated_at.asc())
            .limit(limit)
            .all()
        )

        recovered = []
        skipped = []
        failed = []

        for task in candidates:
            celery_id = str(task.celery_task_id or '')
            already_queued = (
                celery_id in queue_refs['celery_ids']
                or str(task.id) in queue_refs['publication_task_ids']
            )
            if already_queued:
                skipped.append({
                    'task_id': task.id,
                    'celery_id': task.celery_task_id,
                    'reason': 'already_in_render_queue',
                })
                continue

            try:
                old_celery_id = task.celery_task_id
                celery_task = _enqueue_publication_task(task)
                recovered.append({
                    'task_id': task.id,
                    'old_celery_id': old_celery_id,
                    'new_celery_id': celery_task.id,
                })
            except Exception as exc:
                db.session.rollback()
                failed.append({
                    'task_id': task.id,
                    'error': str(exc),
                })

        processing_repair = repair_stale_publisher_processing_tasks_once(limit=limit)
        if recovered or processing_repair.get('requeued') or processing_repair.get('closed'):
            _clear_publisher_task_list_cache()

        return jsonify({
            'success': True,
            'data': {
                'max_age_minutes': max_age_minutes,
                'queue_length': queue_refs['queue_length'],
                'checked': len(candidates),
                'recovered': recovered,
                'skipped': skipped,
                'failed': failed,
                'processing_repair': processing_repair,
            }
        })

    except Exception as e:
        logger.error(f"Recover pending tasks failed: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@bp.route('/post-youtube/<task_id>', methods=['POST'])
def post_to_youtube(task_id):
    """
    Directly upload an existing task's video to YouTube.
    """
    from services.platform_publish_dispatch import dispatch_platform_publish
    
    try:
        task = _get_publication_task(
            task_id,
            PublicationTask.id,
            PublicationTask.result,
        )
        if not task:
            return jsonify({'error': 'Task not found'}), 404
            
        if not task.result or not task.result.get('video_path'):
            return jsonify({'error': 'No video file found for this task. It must be completed first.'}), 400
            
        submission = dispatch_platform_publish("youtube", task.id)
        
        return jsonify({
            'success': True,
            'message': 'YouTube upload started',
            'task_id': task.id,
            'celery_id': submission["celery_id"]
        })
        
    except Exception as e:
        logger.error(f"Post to YouTube failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@bp.route('/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """
    Get status of a specific task.
    """
    task = _get_publication_task(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
        
    return jsonify({
        'success': True,
        'data': task.to_dict()
    })

@bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    List recent publisher/video tasks (pagination could be added).
    """
    include_all = str(request.args.get('include_all', '')).lower() in ('1', 'true', 'yes')
    include_payload = str(request.args.get('include_payload', '')).lower() in ('1', 'true', 'yes')
    cache_key = (include_all,)
    if not include_payload:
        cached = _get_publisher_task_list_cache(cache_key)
        if cached is not None:
            return jsonify(cached)

    query = PublicationTask.query
    if not include_all:
        query = query.filter(PublicationTask.feature == 'publisher')

    if include_payload:
        tasks = query.order_by(PublicationTask.created_at.desc()).limit(20).all()
        data = [t.to_dict() for t in tasks]
    else:
        rows = (
            query.with_entities(
                PublicationTask.id.label('id'),
                PublicationTask.celery_task_id.label('celery_task_id'),
                PublicationTask.status.label('status'),
                PublicationTask.current_step.label('current_step'),
                PublicationTask.progress.label('progress'),
                PublicationTask.error_message.label('error_message'),
                PublicationTask.created_at.label('created_at'),
                PublicationTask.updated_at.label('updated_at'),
                PublicationTask.completed_at.label('completed_at'),
                PublicationTask.feature.label('feature'),
                PublicationTask.task_title.label('task_title'),
                PublicationTask.task_video_path.label('video_path'),
            )
            .order_by(PublicationTask.created_at.desc())
            .limit(20)
            .all()
        )
        data = [_publication_task_summary(row) for row in rows]

    payload = {
        'success': True,
        'data': data
    }
    if not include_payload:
        _set_publisher_task_list_cache(cache_key, payload)
    return jsonify(payload)
